"""Convert a 16-bit PCM WAV to 8 kHz mono (phone/narrowband) per the docs' accepted formats."""
import wave, audioop, os

def to_phone_wav(src_wav, dst_wav):
    w = wave.open(src_wav, "rb")
    n, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
    frames = w.readframes(w.getnframes()); w.close()
    if width != 2:
        frames = audioop.lin2lin(frames, width, 2); width = 2
    if n == 2:
        frames = audioop.tomono(frames, width, 0.5, 0.5)
    frames, _ = audioop.ratecv(frames, width, 1, rate, 8000, None)
    out = wave.open(dst_wav, "wb"); out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000)
    out.writeframes(frames); out.close()
    return dst_wav
