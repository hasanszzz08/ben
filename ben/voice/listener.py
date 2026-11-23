"""Voice listener and Text-to-Speech (TTS) module for BEN — Phase 7.

Uses:
- SpeechRecognition for microphone input and keyword detection
- pyttsx3 for offline text-to-speech feedback
- Fuzzy matching to reliably detect "hello ben aslamlyekum" or variations
"""

import asyncio
import difflib
import re
import threading
from typing import Optional

from ben.utils.logger import logger

# Lazy load or gracefully fallback for audio dependencies
try:
    import speech_recognition as sr
except ImportError:
    sr = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

_tts_lock = threading.Lock()


def fuzzy_match_codeword(transcript: str, target: str = "aslamlyekum", threshold: float = 0.65) -> bool:
    """Check if spoken transcript contains the codeword using fuzzy matching."""
    if not transcript:
        return False

    clean_text = re.sub(r"[^\w\s]", "", transcript.lower()).strip()
    clean_target = re.sub(r"[^\w\s]", "", target.lower()).strip()

    # Exact or substring match
    if clean_target in clean_text:
        return True

    # Known common phonetic variations for "aslamlyekum"
    phonetic_variants = [
        "aslamlyekum",
        "aslamlekum",
        "assalamualaykum",
        "assalam",
        "aslam",
        "alaykum",
        "alekum",
        "hello ben aslamlyekum",
        "aslamlykum",
    ]
    for variant in phonetic_variants:
        if variant in clean_text:
            return True

    # Fuzzy sequence ratio on words and n-grams
    words = clean_text.split()
    for word in words:
        ratio = difflib.SequenceMatcher(None, word, clean_target).ratio()
        if ratio >= threshold:
            return True

    # Compare entire string ratio
    full_ratio = difflib.SequenceMatcher(None, clean_text, f"hello ben {clean_target}").ratio()
    if full_ratio >= threshold:
        return True

    return False


def _sync_listen(codeword: str = "aslamlyekum", timeout_seconds: int = 5) -> bool:
    """Listen to microphone synchronously for codeword."""
    if sr is None:
        logger.warning("SpeechRecognition not installed. Voice detection unavailable.")
        return False

    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True

    try:
        # Check if microphone is available
        with sr.Microphone() as source:
            logger.info("Listening for voice codeword 'aslamlyekum'...")
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            try:
                audio = recognizer.listen(source, timeout=timeout_seconds, phrase_time_limit=5)
            except sr.WaitTimeoutError:
                logger.debug("Voice listening timed out with no speech detected.")
                return False

            try:
                transcript = recognizer.recognize_google(audio)
                logger.info(f"Speech heard: '{transcript}'")
                detected = fuzzy_match_codeword(transcript, codeword)
                if detected:
                    logger.info("Codeword 'aslamlyekum' detected successfully!")
                return detected
            except sr.UnknownValueError:
                logger.debug("Speech was unintelligible.")
                return False
            except Exception as e:
                logger.warning(f"Speech recognition service error: {e}")
                return False

    except (OSError, AttributeError) as mic_err:
        logger.warning(f"Microphone device error: {mic_err}. (Simulating test mode if no hardware mic connected)")
        return False
    except Exception as exc:
        logger.error(f"Voice listener error: {exc}")
        return False


async def listen_for_codeword(codeword: str = "aslamlyekum", timeout_seconds: int = 5) -> bool:
    """Asynchronously listen for the codeword on the microphone."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_listen, codeword, timeout_seconds)


def speak(text: str) -> bool:
    """Speak text to the user via pyttsx3 TTS."""
    if not text:
        return False

    logger.info(f"[TTS SPEAK]: {text}")

    if pyttsx3 is None:
        return False

    with _tts_lock:
        com_initialized = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            com_initialized = True
        except Exception:
            pass

        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.setProperty("volume", 0.9)
            engine.say(text)
            engine.runAndWait()
            return True
        except Exception as exc:
            logger.warning(f"TTS engine error: {exc}")
            return False
        finally:
            if com_initialized:
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
