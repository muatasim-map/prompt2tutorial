"""Shared test fixtures. Ensures the ``src`` package is importable and that no
test makes live LLM/TTS/network calls (everything is mocked)."""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _reset_llm_state():
    """Reset process-global cooldown/concurrency state between tests."""
    import llm_service
    llm_service.reset_reliability_state()
    yield
    llm_service.reset_reliability_state()
