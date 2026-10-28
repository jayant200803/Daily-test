"""Live, interactive Atlas demo — speak to test STT, type to test TTS.

This is NOT the Day-1 QA harness (run_day1.py). It does not score anything.
It is a hands-on tool: talk into your mic and see what atlas-listen heard,
or type a sentence and hear atlas-speak say it back.

Both paths use the exact documented endpoints via lib/atlas.py:
  STT  -> POST /v1/audio/speech's sibling  /v1/audio/transcriptions
  TTS  -> POST /v1/audio/speech

Requires: sounddevice, numpy  (pip install -r requirements-demo.txt)
"""
import os, sys, wave, time, tempfile

try:
    import sounddevice as sd
    import numpy as np
except ImportError:
    sys.exit("Live demo needs sounddevice + numpy.\n"
             "Install them with:  python -m pip install --user -r requirements-demo.txt")

# Load .env so ATLAS_API_KEY is available (same as run_day1.py).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import SPEAK_VOICE
from lib import atlas

# Atlas accepts WAV 16-bit PCM mono 8-48 kHz. Record mono int16 at 16 kHz.
SAMPLE_RATE = 16000
AUDIO_OUT = os.path.join(os.path.dirname(__file__), "audio_out")
os.makedirs(AUDIO_OUT, exist_ok=True)


def _record_wav(seconds, path):
    """Record `seconds` of mic audio to a 16-bit mono WAV at SAMPLE_RATE."""
    print(f"  recording {seconds}s... speak now.")
    frames = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                    channels=1, dtype="int16")
    sd.wait()
    print("  done recording.")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)          # 16-bit
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames.tobytes())
    return path


def _play_wav(path):
    """Play a WAV file out the default speakers."""
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        data = w.readframes(w.getnframes())
    audio = np.frombuffer(data, dtype=np.int16)
    sd.play(audio, rate)
    sd.wait()


def stt_live():
    """Record from the mic, send to atlas-listen, print the transcript."""
    try:
        secs = input("  seconds to record [30]: ").strip()
        secs = int(secs) if secs else 30
    except ValueError:
        secs = 30
    lang = input("  language [en]: ").strip() or "en"
    prompt = input("  optional vocabulary hint (comma words, blank=none): ").strip()

    clip = os.path.join(AUDIO_OUT, "live_mic.wav")
    _record_wav(secs, clip)

    print("  sending to atlas-listen ...")
    res = atlas.transcribe(clip, language=lang,
                           prompt=prompt or None,
                           content_type="audio/wav")
    if res.get("status") == 200 and "text" in res:
        print("\n  >>> Atlas heard: \"%s\"\n" % res["text"].strip())
        if res.get("usage"):
            print("  usage:", res["usage"])
    else:
        print("\n  [error] status=%s  %s\n" % (res.get("status"), res.get("raw") or res))


def tts_live():
    """Type text, synthesize with atlas-speak, play it back."""
    text = input("  text to speak: ").strip()
    if not text:
        print("  (nothing to say)")
        return
    voice = input(f"  voice [{SPEAK_VOICE}]: ").strip() or SPEAK_VOICE

    out = os.path.join(AUDIO_OUT, "live_speak.wav")
    print("  sending to atlas-speak ...")
    res = atlas.synthesize(text, voice=voice, out_path=out, response_format="wav")
    if res.get("status") == 200 and res.get("bytes"):
        print("  got %d bytes (first-audio header=%sms). playing..."
              % (res["bytes"], res.get("first_audio_header_ms")))
        _play_wav(out)
        print("  saved to", out)
    else:
        print("  [error] status=%s  %s" % (res.get("status"), res))


def main():
    print("=" * 60)
    print("ATLAS LIVE DEMO  (speak to test STT, type to test TTS)")
    print("Endpoints are exactly per the Atlas docs. Nothing is scored.")
    print("=" * 60)
    # Quick connectivity check so a bad key fails clearly up front.
    code, _ = atlas.list_models()
    if code != 200:
        sys.exit(f"Cannot reach Atlas (GET /v1/models -> {code}). Check ATLAS_API_KEY / network.")
    print("connected OK (GET /v1/models -> 200)\n")

    while True:
        print("  1) speak  -> atlas-listen (STT)")
        print("  2) type   -> atlas-speak  (TTS)")
        print("  q) quit")
        choice = input("choose: ").strip().lower()
        print()
        if choice == "1":
            stt_live()
        elif choice == "2":
            tts_live()
        elif choice in ("q", "quit", "exit"):
            print("bye.")
            return
        else:
            print("  pick 1, 2, or q.")
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye.")
