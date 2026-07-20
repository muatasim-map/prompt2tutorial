import os
import subprocess
import re
import asyncio
import edge_tts
import hashlib
import json
import shutil
from pathlib import Path
from openai import OpenAI
from abc import ABC, abstractmethod


def get_audio_duration(audio_path):
    """
    Gets the duration of an audio file using ffprobe
    
    Args:
        audio_path: Path to the audio file
    
    Returns:
        Duration in seconds (float) or None if error
    """
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            duration = float(result.stdout.strip())
            return duration
        else:
            print(f"  [WARNING] Could not get duration for {audio_path}")
            return None
    except Exception as e:
        print(f"  [WARNING] Error getting audio duration: {e}")
        return None


def detect_language(text):
    """
    Simple heuristic language detection for Spanish and English
    Returns:
        "es-ES-AlvaroNeural" or "en-US-AndrewMultilingualNeural"
    """
    if not text:
        return "en-US-AndrewMultilingualNeural"
    
    # Spanish specific accents and common character indicators
    spanish_indicators = ['á', 'é', 'í', 'ó', 'ú', 'ñ', 'ü', '¿', '¡']
    text_lower = text.lower()
    
    for char in spanish_indicators:
        if char in text_lower:
            return "es-ES-AlvaroNeural"
            
    # Common Spanish stop words
    spanish_words = {'el', 'la', 'de', 'que', 'en', 'un', 'una', 'los', 'las', 'por', 'para', 'con', 'como'}
    # Tokenize words roughly
    words = set(re.findall(r'\b\w+\b', text_lower))
    if words.intersection(spanish_words):
        return "es-ES-AlvaroNeural"
        
    return "en-US-AndrewMultilingualNeural"


async def generate_edge_tts_fragment_async(text, output_file, voice, rate="+0%"):
    """Asynchronous function to generate Edge TTS media file"""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_file)


class TTSProvider(ABC):
    @abstractmethod
    def generate_fragment(self, text, index, output_dir="media/audio_fragments"):
        """
        Generates an audio fragment from text.
        Returns:
            Tuple of (audio_path, duration) or (None, None) if error
        """
        pass

    @abstractmethod
    def get_settings(self):
        """
        Returns a dict of all configuration options affecting output sound.
        """
        pass


class OpenAITTSProvider(TTSProvider):
    def __init__(self, client, model="tts-1", voice="alloy"):
        self.client = client
        self.model = model
        self.voice = voice

    def get_settings(self):
        return {
            "provider": "openai",
            "model": self.model,
            "voice": self.voice
        }

    def generate_fragment(self, text, index, output_dir="media/audio_fragments"):
        if not self.client or not os.getenv("OPENAI_API_KEY"):
            print("  [INFO] OpenAI API key or client missing for OpenAITTSProvider.")
            return None, None
            
        try:
            os.makedirs(output_dir, exist_ok=True)
            audio_path = os.path.join(output_dir, f"fragment_{index}.mp3")
            
            print(f"  Generating audio fragment {index} via OpenAI...")
            print(f"    Text preview: {text[:80]}...")
            
            response = self.client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=text
            )
            response.stream_to_file(audio_path)
            
            duration = get_audio_duration(audio_path)
            if duration:
                print(f"  [OK] OpenAI TTS fragment saved: {audio_path} (duration: {duration:.2f}s)")
            else:
                word_count = len(text.split())
                duration = max(2.5, word_count / 2.3)
                print(f"  [WARNING] Could not determine actual duration. Using estimated: {duration:.2f}s")
                
            return audio_path, duration
        except Exception as e:
            print(f"  [ERROR] OpenAI TTS failed: {e}")
            return None, None


class EdgeTTSProvider(TTSProvider):
    def __init__(self, voice="auto", rate=None):
        self.voice = voice or "auto"
        # Fallback rate configuration from env if not provided
        self.rate = rate or os.getenv("EDGE_TTS_RATE", "+0%")

    def get_settings(self):
        return {
            "provider": "edge",
            "voice": self.voice,
            "rate": self.rate
        }

    def generate_fragment(self, text, index, output_dir="media/audio_fragments"):
        try:
            os.makedirs(output_dir, exist_ok=True)
            audio_path = os.path.join(output_dir, f"fragment_{index}.mp3")
            
            # If voice is auto or empty, detect the language voice
            resolved_voice = self.voice
            if resolved_voice == "auto" or not resolved_voice:
                resolved_voice = detect_language(text)
                
            print(f"  Generating Edge TTS fragment {index} (voice: {resolved_voice}, rate: {self.rate})...")
            print(f"    Text preview: {text[:80]}...")
            
            asyncio.run(generate_edge_tts_fragment_async(text, audio_path, resolved_voice, self.rate))
            
            duration = get_audio_duration(audio_path)
            if duration:
                print(f"  [OK] Edge TTS fragment saved: {audio_path} (duration: {duration:.2f}s)")
            else:
                # Parse speech rate percentage to scale duration estimate accurately
                rate_percent = 0.0
                if self.rate:
                    match_rate = re.search(r'([+-]?\d+)\s*%', self.rate)
                    if match_rate:
                        rate_percent = float(match_rate.group(1))
                
                # Base speech speed: 2.6 words per second
                words_per_second = 2.6 * (1.0 + rate_percent / 100.0)
                words_per_second = max(1.0, words_per_second)
                
                word_count = len(text.split())
                duration = max(2.2, word_count / words_per_second)
                print(f"  [WARNING] Could not determine duration via ffprobe. Using rate-scaled estimate ({words_per_second:.2f} wps): {duration:.2f}s")
                
            return audio_path, duration
        except Exception as e:
            print(f"  [ERROR] Edge TTS fragment generation failed: {e}")
            return None, None


class FallbackTTSProvider(TTSProvider):
    def __init__(self, providers):
        self.providers = providers

    def get_settings(self):
        return {
            "provider": "fallback",
            "providers": [p.get_settings() for p in self.providers]
        }

    def generate_fragment(self, text, index, output_dir="media/audio_fragments"):
        for provider in self.providers:
            audio_path, duration = provider.generate_fragment(text, index, output_dir)
            if audio_path and duration:
                return audio_path, duration
        return None, None


class CachingTTSProvider(TTSProvider):
    def __init__(self, provider, cache_dir="media/tts_cache"):
        self.provider = provider
        self.cache_dir = cache_dir
        self.cache_enabled = os.getenv("TTS_CACHE_ENABLED", "true").lower() == "true"

    def get_settings(self):
        return {
            "cached_provider": self.provider.get_settings(),
            "cache_dir": self.cache_dir
        }

    def generate_fragment(self, text, index, output_dir="media/audio_fragments", bypass_cache=False, status_callback=None, total_scenes=1):
        if not self.cache_enabled or bypass_cache:
            if bypass_cache:
                print(f"  [CACHE BYPASS] Forcing regeneration for scene {index}")
            if status_callback:
                progress = 30 + (index / total_scenes) * 10
                status_callback(progress=progress, message=f"→ Scene {index}/{total_scenes} (TTS Generation)")
            return self.provider.generate_fragment(text, index, output_dir)
            
        # Generate content-based cache key
        settings = self.provider.get_settings()
        hash_data = {
            "text": text,
            "settings": settings
        }
        
        serialized = json.dumps(hash_data, sort_keys=True)
        cache_key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        
        os.makedirs(self.cache_dir, exist_ok=True)
        cache_mp3 = os.path.join(self.cache_dir, f"{cache_key}.mp3")
        cache_json = os.path.join(self.cache_dir, f"{cache_key}.json")
        
        # Check cache hit
        if os.path.exists(cache_mp3) and os.path.exists(cache_json):
            try:
                with open(cache_json, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                duration = metadata.get("duration")
                if duration:
                    os.makedirs(output_dir, exist_ok=True)
                    target_path = os.path.join(output_dir, f"fragment_{index}.mp3")
                    shutil.copyfile(cache_mp3, target_path)
                    print(f"  [CACHE HIT] Reused cached TTS for scene {index} (duration: {duration:.2f}s, key: {cache_key[:12]})")
                    if status_callback:
                        progress = 30 + (index / total_scenes) * 10
                        status_callback(progress=progress, message=f"✓ Scene {index}/{total_scenes} (TTS Cache: {cache_key[:8]}...)")
                    return target_path, duration
            except Exception as e:
                print(f"  [WARNING] Failed to load cache metadata: {e}")
                
        # Cache miss: generate normally
        if status_callback:
            progress = 30 + (index / total_scenes) * 10
            status_callback(progress=progress, message=f"→ Scene {index}/{total_scenes} (TTS Generation)")
            
        audio_path, duration = self.provider.generate_fragment(text, index, output_dir)
        
        if audio_path and duration and os.path.exists(audio_path):
            try:
                shutil.copyfile(audio_path, cache_mp3)
                metadata = {
                    "text": text,
                    "settings": settings,
                    "duration": duration
                }
                with open(cache_json, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=2)
                print(f"  [CACHE STORE] Saved TTS fragment to cache (key: {cache_key[:12]})")
                if status_callback:
                    progress = 30 + (index / total_scenes) * 10
                    status_callback(progress=progress, message=f"✓ Scene {index}/{total_scenes} (TTS Generated: {cache_key[:8]}...)")
            except Exception as e:
                print(f"  [WARNING] Failed to write cache entry: {e}")
                
        return audio_path, duration


def generate_audio_fragment(client, text, index, output_dir="media/audio_fragments", tts_provider=None, tts_model="tts-1", voice="alloy", tts_voice=None, tts_rate=None, bypass_cache=False, status_callback=None, total_scenes=1):
    """
    Wrapper function that instantiates and decodes caching providers
    """
    provider_name = tts_provider or os.getenv("TTS_PROVIDER", "openai").lower()
    
    openai_provider = OpenAITTSProvider(client, model=tts_model, voice=voice)
    edge_provider = EdgeTTSProvider(voice=tts_voice, rate=tts_rate)
    
    if provider_name == "edge":
        base_provider = edge_provider
    elif provider_name == "openai":
        base_provider = FallbackTTSProvider([openai_provider, edge_provider])
    else:
        base_provider = FallbackTTSProvider([openai_provider, edge_provider])
        
    caching_provider = CachingTTSProvider(base_provider)
    return caching_provider.generate_fragment(text, index, output_dir, bypass_cache=bypass_cache, status_callback=status_callback, total_scenes=total_scenes)


def concatenate_audio_fragments(audio_paths, output_path="media/audio.mp3"):
    """
    Concatenates multiple audio fragments into a single MP3 file using binary concatenation
    
    Args:
        audio_paths: List of paths to audio fragments
        output_path: Path for the final concatenated audio file
    
    Returns:
        True if successful, False otherwise
    """
    if not audio_paths:
        print("[ERROR] No audio fragments to concatenate")
        return False
    
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        print(f"\n  Concatenating {len(audio_paths)} audio fragments via binary copy...")
        
        with open(output_path, 'wb') as outfile:
            for path in audio_paths:
                if os.path.exists(path):
                    with open(path, 'rb') as infile:
                        outfile.write(infile.read())
                        
        print(f"  [OK] Final audio created: {output_path}")
        return True
    except Exception as e:
        print(f"  [ERROR] Binary audio concatenation failed: {e}")
        return False


def generate_complete_audio(client, video_data, tts_provider=None, tts_model="tts-1", voice="alloy", tts_voice=None, tts_rate=None, bypass_cache=False, status_callback=None):
    """
    Generates complete audio for all scenes
    
    Returns:
        Tuple of (audio_path, durations_dict) where durations_dict maps scene index to duration
    """
    selected_provider = tts_provider or os.getenv("TTS_PROVIDER", "openai").lower()
    print(f"\n{'='*80}")
    print(f"GENERATING AUDIO WITH TTS")
    print(f"{'='*80}")
    print(f"Primary Provider: {selected_provider.upper()}")
    if selected_provider == "openai" and client and os.getenv("OPENAI_API_KEY"):
        print(f"Model: {tts_model}")
        print(f"Voice: {voice}")
    elif selected_provider == "edge":
        print(f"Voice: {tts_voice or 'auto'}")
        print(f"Rate: {tts_rate or 'default'}")
    print(f"Scenes: {len(video_data)}\n")
    
    audio_fragments = []
    audio_durations = {}
    total_scenes = len(video_data)
    
    for index, scene_data in enumerate(video_data, 1):
        text = scene_data.get('text', '')
        
        if not text:
            print(f"  [WARNING] Scene {index} has no text, skipping...")
            continue
        
        audio_path, duration = generate_audio_fragment(
            client=client,
            text=text,
            index=index,
            tts_provider=tts_provider,
            tts_model=tts_model,
            voice=voice,
            tts_voice=tts_voice,
            tts_rate=tts_rate,
            bypass_cache=bypass_cache,
            status_callback=status_callback,
            total_scenes=total_scenes
        )
        
        if audio_path and os.path.exists(audio_path):
            audio_fragments.append(audio_path)
            if duration:
                audio_durations[index] = duration
        else:
            print(f"  [WARNING] Could not generate audio for scene {index}")
    
    if audio_fragments:
        print(f"\n{'='*80}")
        print(f"CONCATENATING {len(audio_fragments)} AUDIO FRAGMENTS")
        print(f"{'='*80}")
        
        output_path = "media/audio.mp3"
        success = concatenate_audio_fragments(audio_fragments, output_path)
        
        if success:
            print(f"\n[OK] Complete audio generated: {output_path}\n")
            return output_path, audio_durations
        else:
            print(f"\n[ERROR] Failed to concatenate audio fragments\n")
            return None, {}
    else:
        print(f"\n[ERROR] No audio fragments were generated\n")
        return None, {}
