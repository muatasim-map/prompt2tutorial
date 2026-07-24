"""Centralized LLM invocation layer with Gemini reliability controls.

Responsibilities:

* One place that talks to Gemini / Claude / OpenAI, driven by :mod:`config`
  role-based model routing.
* **Gemini transient-failure handling** that does not stack duplicate retry
  systems: the ``google-genai`` SDK's own tenacity-based retry (configured via
  ``HttpRetryOptions``) provides bounded exponential backoff with jitter for
  429/5xx/timeout. This module adds, *around* that single retry system:
    - a per-model cooldown after repeated 429 / ``RESOURCE_EXHAUSTED``;
    - a bounded in-process concurrency limit (semaphore);
    - fallback to a configured model only after the primary's retry policy is
      exhausted — never a silent quality downgrade;
    - structured, secret-free status events (selected model, attempt/category,
      cooldown state, fallback reason).

No API keys, auth headers, or raw prompt/response content are ever logged.
"""

from __future__ import annotations

import random
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from config import ModelRoles, RetryPolicy, gemini_api_key, get_retry_policy

StatusCallback = Optional[Callable[[dict], None]]

# Error categories surfaced to callers / status (safe, non-sensitive labels).
CAT_RATE_LIMIT = "rate_limit"
CAT_TRANSIENT = "transient"
CAT_TIMEOUT = "timeout"
CAT_UNAVAILABLE_MODEL = "unavailable_model"
CAT_INVALID_OUTPUT = "invalid_output"
CAT_BAD_REQUEST = "bad_request"
CAT_OTHER = "other"

_RETRIABLE_HTTP = [408, 429, 500, 502, 503, 504]

# Categories worth retrying on the SAME model. ``bad_request`` (a malformed
# request such as an unsupported response schema) and ``unavailable_model``
# (404) are deterministic — retrying them only wastes quota and time.
_RETRYABLE_CATEGORIES = frozenset(
    {CAT_RATE_LIMIT, CAT_TRANSIENT, CAT_TIMEOUT, CAT_INVALID_OUTPUT}
)


class LLMError(RuntimeError):
    """A safe, categorized LLM failure (no secrets in the message)."""

    def __init__(self, message: str, category: str = CAT_OTHER, model: Optional[str] = None):
        super().__init__(message)
        self.category = category
        self.model = model


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    provider: str
    used_fallback: bool = False
    fallback_reason: Optional[str] = None
    # Real token usage from the provider's own response (0 when the SDK didn't
    # report it, e.g. a provider without usage metadata) — feeds cost/analytics
    # reporting. Never estimated/guessed: either the provider told us, or it's 0.
    input_tokens: int = 0
    output_tokens: int = 0


# --------------------------------------------------------------------------- #
# Process-global cooldown + concurrency state (shared across job threads)
# --------------------------------------------------------------------------- #
_state_lock = threading.Lock()
_cooldown_until: dict = {}      # model_id -> epoch seconds until which it is cooled down
_rate_limit_hits: dict = {}     # model_id -> consecutive 429 count
_semaphore: Optional[threading.BoundedSemaphore] = None
_semaphore_limit: int = 0


def _get_semaphore(limit: int) -> threading.BoundedSemaphore:
    global _semaphore, _semaphore_limit
    with _state_lock:
        if _semaphore is None or _semaphore_limit != limit:
            _semaphore = threading.BoundedSemaphore(limit)
            _semaphore_limit = limit
        return _semaphore


def _now() -> float:
    return time.time()


_QUOTA_ID_RE = re.compile(r"['\"]quotaId['\"]\s*:\s*['\"]([^'\"]+)['\"]")
_QUOTA_VALUE_RE = re.compile(r"['\"]quotaValue['\"]\s*:\s*['\"]([^'\"]+)['\"]")
_RETRY_DELAY_RE = re.compile(r"['\"]retryDelay['\"]\s*:\s*['\"](\d+)s['\"]")


def quota_details(exc: BaseException) -> dict:
    """Extract safe quota metadata from a Gemini 429 (no secrets involved).

    Turns an opaque "rate_limit" into something actionable: *which* quota was hit
    (per-minute vs per-day, free tier vs paid), its limit, and the server's own
    suggested retry delay.
    """
    raw = str(exc)
    out: dict = {}
    for key, pattern in (("quota_id", _QUOTA_ID_RE), ("quota_value", _QUOTA_VALUE_RE)):
        match = pattern.search(raw)
        if match:
            out[key] = match.group(1)
    delay = _RETRY_DELAY_RE.search(raw)
    if delay:
        out["retry_after_seconds"] = int(delay.group(1))
    qid = out.get("quota_id", "")
    if "PerDay" in qid:
        out["scope"] = "per-day"
    elif "PerMinute" in qid:
        out["scope"] = "per-minute"
    if "FreeTier" in qid:
        out["tier"] = "free"
    return out


def _safe_error_message(exc: BaseException) -> str:
    """Produce a short, secret-free description of an exception."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    name = exc.__class__.__name__
    base = f"{name}: {status or ''} ({code})".strip() if (code or status) else name

    quota = quota_details(exc)
    if quota:
        bits = []
        if quota.get("quota_id"):
            bits.append(f"quota={quota['quota_id']}")
        if quota.get("quota_value"):
            bits.append(f"limit={quota['quota_value']}")
        if quota.get("retry_after_seconds"):
            bits.append(f"retry_after={quota['retry_after_seconds']}s")
        if quota.get("scope") == "per-day":
            bits.append("NOTE: this is a DAILY cap — it will not clear by retrying now")
        base = f"{base} [{'; '.join(bits)}]"
    return base


def _categorize(exc: BaseException) -> str:
    """Map an exception to a safe error category.

    HTTP 400 / ``INVALID_ARGUMENT`` means *our request* was malformed (for
    example an unsupported ``response_schema``) — it is deliberately NOT
    reported as ``unavailable_model``, which previously disguised a schema bug
    as a missing model.
    """
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "").upper()
    message = str(getattr(exc, "message", "") or "").upper()
    name = exc.__class__.__name__.upper()

    if code == 429 or "RESOURCE_EXHAUSTED" in status or "RESOURCE_EXHAUSTED" in message:
        return CAT_RATE_LIMIT
    if code == 404 or "NOT_FOUND" in status:
        return CAT_UNAVAILABLE_MODEL
    if code == 400 or "INVALID_ARGUMENT" in status:
        return CAT_BAD_REQUEST
    if code in (500, 502, 503, 504) or isinstance(exc, genai_errors.ServerError):
        return CAT_TRANSIENT
    if "TIMEOUT" in name or "TIMEOUT" in message:
        return CAT_TIMEOUT
    # Transport-level connection resets are transient and worth retrying.
    if "CONNECT" in name or "CONNECTION" in name:
        return CAT_TRANSIENT
    return CAT_OTHER


class LLMService:
    """Provider-agnostic generation entry point with Gemini reliability logic."""

    def __init__(
        self,
        roles: ModelRoles,
        policy: Optional[RetryPolicy] = None,
        status: StatusCallback = None,
        strict: bool = False,
    ) -> None:
        self.roles = roles
        self.policy = policy or get_retry_policy()
        self.status = status
        # Strict mode: retry ONLY the exact selected model; never switch model
        # or provider, never use a fallback.
        self.strict = strict
        self._gemini_client: Optional[genai.Client] = None

    # -- status ---------------------------------------------------------- #
    def _emit(self, **fields: Any) -> None:
        if self.status:
            try:
                self.status(fields)
            except Exception:  # status must never break generation
                pass

    # -- cooldown bookkeeping ------------------------------------------- #
    def _is_cooling(self, model: str) -> bool:
        with _state_lock:
            until = _cooldown_until.get(model, 0)
            return until > _now()

    def _cooldown_remaining(self, model: str) -> float:
        with _state_lock:
            return max(0.0, _cooldown_until.get(model, 0) - _now())

    def _record_success(self, model: str) -> None:
        with _state_lock:
            _rate_limit_hits.pop(model, None)
            _cooldown_until.pop(model, None)

    def _record_rate_limit(self, model: str) -> float:
        """Increment 429 counter; set cooldown when the threshold is crossed.

        Returns the cooldown seconds applied (0 if not yet cooling down).
        """
        with _state_lock:
            hits = _rate_limit_hits.get(model, 0) + 1
            _rate_limit_hits[model] = hits
            if hits >= self.policy.cooldown_threshold:
                _cooldown_until[model] = _now() + self.policy.cooldown_seconds
                _rate_limit_hits[model] = 0
                return self.policy.cooldown_seconds
        return 0.0

    # -- concurrency ----------------------------------------------------- #
    @contextmanager
    def _concurrency_guard(self):
        sem = _get_semaphore(self.policy.max_concurrency)
        sem.acquire()
        try:
            yield
        finally:
            sem.release()

    # -- gemini ---------------------------------------------------------- #
    def _client(self) -> genai.Client:
        if self._gemini_client is None:
            key = gemini_api_key()
            if not key:
                raise LLMError(
                    "Gemini API key not configured", category=CAT_UNAVAILABLE_MODEL
                )
            # IMPORTANT: this layer owns retrying so that attempt number and
            # backoff wait are *visible* in job progress. The SDK's own retry is
            # therefore limited to a single attempt — the two retry systems must
            # never stack, which would multiply wait time and burn quota.
            retry_options = genai_types.HttpRetryOptions(
                attempts=1,
                httpStatusCodes=_RETRIABLE_HTTP,
            )
            self._gemini_client = genai.Client(
                api_key=key,
                http_options=genai_types.HttpOptions(
                    retry_options=retry_options,
                    timeout=self.policy.http_timeout_ms,
                ),
            )
        return self._gemini_client

    def _raw_gemini_call(
        self,
        model: str,
        system: str,
        prompt: str,
        response_schema: Any = None,
    ) -> Tuple[str, int, int]:
        """Returns ``(text, input_tokens, output_tokens)``.

        Token counts come straight from the response's own ``usage_metadata`` —
        this is the SDK reporting what it actually billed, not an estimate.
        """
        config_kwargs: dict = {
            "system_instruction": system,
            "response_mime_type": "application/json",
        }
        if response_schema is not None:
            # SDK accepts Pydantic models / list[Model] as response_schema.
            config_kwargs["response_schema"] = response_schema
        config = genai_types.GenerateContentConfig(**config_kwargs)
        response = self._client().models.generate_content(
            model=model, contents=prompt, config=config
        )
        text = (response.text or "").strip()
        if not text:
            raise LLMError("empty response from Gemini", category=CAT_INVALID_OUTPUT, model=model)
        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        return text, input_tokens, output_tokens

    def _gemini_candidates(self, role: str) -> List[Tuple[str, bool]]:
        """Models to try, in order. In strict mode this is exactly one model."""
        primary = self.roles.for_role(role)
        candidates: List[Tuple[str, bool]] = [(primary, False)]
        if self.strict:
            return candidates  # never switch model/provider in strict mode
        fb = self.roles.fallback
        if self.policy.fallback_enabled and fb and fb != primary:
            candidates.append((fb, True))
        return candidates

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter, capped by the policy max delay."""
        base = self.policy.initial_delay * (self.policy.exp_base ** attempt)
        capped = min(base, self.policy.max_delay)
        return capped + random.uniform(0.0, self.policy.jitter)

    def _try_model(
        self, model: str, role: str, system: str, prompt: str, response_schema: Any
    ) -> Tuple[Optional[str], Optional[LLMError], int, int]:
        """Attempt one model with bounded, *visible* retries. Same model only.

        Returns ``(text, error, input_tokens, output_tokens)`` — the token
        counts are 0 on any failure path (no call succeeded, nothing to report).
        """
        attempts = max(1, self.policy.attempts)
        last_error: Optional[LLMError] = None
        attempt = 0
        cooldown_waits = 0

        while attempt < attempts:
            if self._is_cooling(model):
                remaining = self._cooldown_remaining(model)
                # A daily quota will never clear by waiting — fail fast with the
                # actionable message instead of stalling the job.
                if last_error is not None and getattr(last_error, "quota_scope", None) == "per-day":
                    return None, last_error, 0, 0
                self._emit(
                    provider="gemini", model=model, role=role, attempt=attempt + 1,
                    cooldown=True, cooldown_seconds=round(remaining, 1),
                    note=f"{model} cooling down ({remaining:.0f}s left)",
                )
                # Strict mode waits the cooldown out rather than downgrading. A
                # pure wait does not consume the attempt budget (bounded by
                # cooldown_waits) so the model still gets its full retries.
                if self.strict and cooldown_waits < attempts:
                    cooldown_waits += 1
                    time.sleep(max(0.0, min(remaining, self.policy.cooldown_seconds)) + 0.25)
                    continue
                return None, last_error or LLMError(f"{model} in cooldown", CAT_RATE_LIMIT, model), 0, 0

            attempt += 1

            self._emit(
                provider="gemini", model=model, role=role, attempt=attempt,
                max_attempts=attempts,
                note=(f"using {model} (attempt {attempt}/{attempts})"
                      if attempt > 1 else f"using {model}"),
            )
            try:
                with self._concurrency_guard():
                    text, in_tok, out_tok = self._raw_gemini_call(model, system, prompt, response_schema)
                self._record_success(model)
                return text, None, in_tok, out_tok
            except LLMError as exc:
                last_error = exc
            except Exception as exc:
                last_error = LLMError(_safe_error_message(exc), _categorize(exc), model)
                # Remember the quota scope so a DAILY cap fails fast instead of
                # sleeping through cooldowns that cannot possibly clear.
                last_error.quota_scope = quota_details(exc).get("scope")

            category = last_error.category
            if category == CAT_RATE_LIMIT:
                self._record_rate_limit(model)
                if getattr(last_error, "quota_scope", None) == "per-day":
                    self._emit(
                        provider="gemini", model=model, role=role, attempt=attempt,
                        error_category=category,
                        note=(f"{model} DAILY quota exhausted — retrying cannot help; "
                              "fails now with an actionable error"),
                    )
                    return None, last_error, 0, 0

            retryable = category in _RETRYABLE_CATEGORIES
            if not retryable or attempt >= attempts:
                self._emit(
                    provider="gemini", model=model, role=role, attempt=attempt,
                    error_category=category,
                    note=(f"{model} failed: {category}"
                          + ("" if retryable else " (not retryable)")),
                )
                return None, last_error, 0, 0

            wait = self._backoff_delay(attempt - 1)
            self._emit(
                provider="gemini", model=model, role=role, attempt=attempt,
                max_attempts=attempts, error_category=category,
                retry_in_seconds=round(wait, 1),
                note=(f"{model} {category}; retry {attempt + 1}/{attempts} "
                      f"in {wait:.1f}s"),
            )
            time.sleep(wait)

        return None, last_error, 0, 0

    def _generate_gemini(
        self, role: str, system: str, prompt: str, response_schema: Any
    ) -> LLMResult:
        last_error: Optional[LLMError] = None

        for model, is_fallback in self._gemini_candidates(role):
            if is_fallback:
                reason = last_error.category if last_error else "primary_failed"
                self._emit(
                    provider="gemini", model=model, role=role,
                    fallback=True, fallback_reason=reason,
                    note=f"falling back to {model} ({reason})",
                )

            text, error, in_tok, out_tok = self._try_model(model, role, system, prompt, response_schema)
            if text is not None:
                return LLMResult(
                    text=text, model=model, provider="gemini",
                    used_fallback=is_fallback,
                    fallback_reason=(last_error.category if (is_fallback and last_error) else None),
                    input_tokens=in_tok, output_tokens=out_tok,
                )
            last_error = error

        if last_error is None:
            last_error = LLMError("Gemini generation failed", CAT_OTHER)
        if self.strict:
            # Make it unmistakable that no downgrade was attempted.
            raise LLMError(
                f"{last_error} [strict mode: retried only {self.roles.for_role(role)}; "
                f"no fallback model or provider was used]",
                category=last_error.category,
                model=last_error.model or self.roles.for_role(role),
            )
        raise last_error

    # -- claude / openai (single-model, SDK-native retry) --------------- #
    def _generate_claude(self, client: Any, system: str, prompt: str) -> LLMResult:
        model = self.roles.script  # single model for all roles
        self._emit(provider="claude", model=model, note=f"using {model}")
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        except Exception as exc:
            raise LLMError(_safe_error_message(exc), category=_categorize(exc), model=model)
        if not text:
            raise LLMError("empty response from Claude", category=CAT_INVALID_OUTPUT, model=model)
        return LLMResult(text=text, model=model, provider="claude",
                         input_tokens=input_tokens, output_tokens=output_tokens)

    def _generate_openai(self, client: Any, system: str, prompt: str) -> LLMResult:
        model = self.roles.script
        self._emit(provider="openai", model=model, note=f"using {model}")
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=16000,
            )
            if not response.choices:
                raise LLMError("no choices in OpenAI response", category=CAT_INVALID_OUTPUT)
            content = response.choices[0].message.content
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(_safe_error_message(exc), category=_categorize(exc), model=model)
        if content is None:
            raise LLMError("OpenAI returned empty content", category=CAT_INVALID_OUTPUT, model=model)
        return LLMResult(text=content.strip(), model=model, provider="openai",
                         input_tokens=input_tokens, output_tokens=output_tokens)

    # -- public API ------------------------------------------------------ #
    def generate(
        self,
        role: str,
        system: str,
        prompt: str,
        provider: str,
        client: Any = None,
        response_schema: Any = None,
    ) -> LLMResult:
        """Generate text for ``role`` using the configured provider.

        For Gemini this applies retry (SDK), cooldown, concurrency limiting and
        configured fallback. For Claude/OpenAI it uses the provider SDK's own
        retry with a single configured model.
        """
        if provider == "gemini":
            result = self._generate_gemini(role, system, prompt, response_schema)
        elif provider == "claude":
            result = self._generate_claude(client, system, prompt)
        elif provider == "openai":
            result = self._generate_openai(client, system, prompt)
        else:
            raise LLMError(f"unknown provider: {provider}", category=CAT_OTHER)

        # Real usage, reported once per successful call — this is what job-level
        # cost/analytics reporting aggregates (see video_generator._make_llm_status).
        self._emit(
            usage=True, provider=result.provider, model=result.model, role=role,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        )
        return result


def reset_reliability_state() -> None:
    """Clear cooldown/concurrency state — used by tests for isolation."""
    global _semaphore, _semaphore_limit
    with _state_lock:
        _cooldown_until.clear()
        _rate_limit_hits.clear()
        _semaphore = None
        _semaphore_limit = 0
