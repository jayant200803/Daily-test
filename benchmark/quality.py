"""Audio-quality + speed analysis for the TTS benchmark.

- duration / RTF  : from the WAV
- naturalness     : Harmonics-to-Noise Ratio (HNR) via Praat (parselmouth), mapped to 1-5
- prosody         : pitch standard deviation (Hz) = expressiveness
- correctness WER : transcribe the generated audio with a neutral ASR (Deepgram) and compare
                    to the input text (WER for English, CER for Japanese)
"""
import io, wave
import numpy as np
import parselmouth
from benchmark import providers as P


def wav_duration_sec(wav):
    # Compute from the ACTUAL data bytes (some providers put a placeholder frame count
    # in the header, which makes getnframes() wrong).
    try:
        w = wave.open(io.BytesIO(wav), "rb")
        rate, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes()); w.close()
        samples = len(data) / float(ch * sw)
        return samples / float(rate)
    except Exception:
        return None


def _sound(wav):
    w = wave.open(io.BytesIO(wav), "rb")
    rate = w.getframerate(); frames = w.readframes(w.getnframes()); w.close()
    arr = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32768.0
    return parselmouth.Sound(arr, sampling_frequency=rate)


def hnr_mean(wav):
    try:
        h = _sound(wav).to_harmonicity()
        vals = h.values[h.values != -200]
        return float(vals.mean()) if len(vals) else None
    except Exception:
        return None


def naturalness_score(hnr):
    if hnr is None:
        return None
    return round(max(1.0, min(5.0, 1 + hnr / 7.5)), 3)


def pitch_std(wav):
    try:
        f = _sound(wav).to_pitch().selected_array["frequency"]
        f = f[f > 0]
        return round(float(f.std()), 2) if len(f) else None
    except Exception:
        return None


def correctness(wav, text, lang):
    """Transcribe with Deepgram (neutral ASR) and score vs input."""
    try:
        hyp = P.stt_deepgram(wav, lang).get("text", "")
    except Exception:
        hyp = ""
    score = P.cer(text, hyp) if lang == "ja" else P.wer(text, hyp)
    return round(score, 3), hyp
