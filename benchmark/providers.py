"""Provider measurement functions for the benchmark — server-friendly (in-memory).

Used by the deployed /benchmark page. Reads keys from env at call time. Returns audio
as bytes so the caller can base64 it to the browser (serverless can't serve saved files).
"""
import os, re, time, socket
import urllib.parse
import requests


def _tcp_rtt_ms(host, port=443, samples=3):
    """Approximate one network round-trip to a host = min TCP-connect time over a few tries.
    Used to subtract network from latency, leaving the provider's processing time."""
    best = None
    for _ in range(samples):
        t = time.time()
        try:
            s = socket.create_connection((host, port), timeout=5); s.close()
        except Exception:
            continue
        ms = int((time.time() - t) * 1000)
        best = ms if best is None else min(best, ms)
    return best

EL_VOICE = "JBFqnCBsd6RMkjVDRZzb"
CART_VOICE_EN = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"
CART_VOICE_JA = "7ca2afba-a719-4f06-9af2-ea2b8e3cf14c"


def _k(name):
    return (os.environ.get(name) or "").strip()


def _timed_stream(url, headers, body):
    """POST streaming; measure first-byte, and also the network round-trip to the host so we
    can report processing-only latency (network removed).
    Returns (first_byte_ms, total_ms, audio_bytes, status, resp_headers, rtt_ms)."""
    host = urllib.parse.urlparse(url).hostname
    rtt = _tcp_rtt_ms(host) if host else None
    t0 = time.time(); first = None; buf = bytearray()
    with requests.post(url, headers=headers, json=body, stream=True, timeout=60) as r:
        if r.status_code != 200:
            return None, None, b"", r.status_code, dict(r.headers), rtt
        for chunk in r.iter_content(chunk_size=1024):
            if chunk:
                if first is None:
                    first = int((time.time() - t0) * 1000)
                buf += chunk
    return first, int((time.time() - t0) * 1000), bytes(buf), 200, dict(r.headers), rtt


def _processing(first_byte_ms, rtt_ms):
    """First-byte minus one network round-trip = provider processing time (estimate, >=0)."""
    if first_byte_ms is None:
        return None
    if rtt_ms is None:
        return first_byte_ms
    return max(0, first_byte_ms - rtt_ms)


# ---- TTS ----
def tts_atlas(text, lang):
    fb, tot, audio, st, hd, rtt = _timed_stream(
        "https://atlas.oneinbox.ai/v1/audio/speech",
        {"Authorization": f"Bearer {_k('ATLAS_API_KEY')}", "Content-Type": "application/json"},
        {"model": "atlas-speak", "voice": "souta", "input": text, "response_format": "mp3"})
    pr = hd.get("X-Atlas-First-Audio-Ms") if isinstance(hd, dict) else None
    return {"first_byte_ms": fb, "processing_ms": _processing(fb, rtt), "rtt_ms": rtt,
            "total_ms": tot, "audio": audio, "status": st,
            "provider_reported_first_audio_ms": int(pr) if pr else None}


def tts_elevenlabs(text, lang):
    model = "eleven_flash_v2_5" if lang == "en" else "eleven_turbo_v2_5"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE}/stream?output_format=mp3_44100_128"
    fb, tot, audio, st, hd, rtt = _timed_stream(
        url, {"xi-api-key": _k("ELEVENLABS_API_KEY"), "Content-Type": "application/json"},
        {"text": text, "model_id": model})
    return {"first_byte_ms": fb, "processing_ms": _processing(fb, rtt), "rtt_ms": rtt,
            "total_ms": tot, "audio": audio, "status": st}


def tts_cartesia(text, lang):
    voice = CART_VOICE_EN if lang == "en" else CART_VOICE_JA
    fb, tot, audio, st, hd, rtt = _timed_stream(
        "https://api.cartesia.ai/tts/bytes",
        {"X-API-Key": _k("CARTESIA_API_KEY"), "Cartesia-Version": "2024-11-13", "Content-Type": "application/json"},
        {"model_id": "sonic-2", "transcript": text, "voice": {"mode": "id", "id": voice},
         "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000}, "language": lang})
    return {"first_byte_ms": fb, "processing_ms": _processing(fb, rtt), "rtt_ms": rtt,
            "total_ms": tot, "audio": audio, "status": st}


def tts_deepgram(text, lang):
    # Deepgram Aura TTS is English-only.
    if lang != "en":
        return {"first_byte_ms": None, "processing_ms": None, "rtt_ms": None,
                "total_ms": None, "audio": b"", "status": "N/A (English only)"}
    fb, tot, audio, st, hd, rtt = _timed_stream(
        "https://api.deepgram.com/v1/speak?model=aura-asteria-en",
        {"Authorization": f"Token {_k('DEEPGRAM_API_KEY')}", "Content-Type": "application/json"},
        {"text": text})
    return {"first_byte_ms": fb, "processing_ms": _processing(fb, rtt), "rtt_ms": rtt,
            "total_ms": tot, "audio": audio, "status": st}


TTS_PROVIDERS = {"Atlas": tts_atlas, "ElevenLabs": tts_elevenlabs,
                 "Cartesia": tts_cartesia, "Deepgram": tts_deepgram}


# ---- STT ----
def gen_stt_clip(text, lang):
    """Generate one clean clip with ElevenLabs; return audio bytes or None."""
    model = "eleven_flash_v2_5" if lang == "en" else "eleven_turbo_v2_5"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE}?output_format=mp3_44100_128"
    r = requests.post(url, headers={"xi-api-key": _k("ELEVENLABS_API_KEY"), "Content-Type": "application/json"},
                      json={"text": text, "model_id": model}, timeout=60)
    return r.content if (r.status_code == 200 and len(r.content) > 500) else None


def stt_atlas(audio, lang):
    import io
    t0 = time.time()
    r = requests.post("https://atlas.oneinbox.ai/v1/audio/transcriptions",
                      headers={"Authorization": f"Bearer {_k('ATLAS_API_KEY')}"},
                      data={"model": "atlas-listen", "language": lang},
                      files={"file": ("clip.mp3", io.BytesIO(audio), "audio/mpeg")}, timeout=60)
    ms = int((time.time() - t0) * 1000)
    try: txt = r.json().get("text", "")
    except Exception: txt = ""
    return {"text": txt, "latency_ms": ms, "status": r.status_code, "mode": "sync"}


def stt_deepgram(audio, lang):
    model = "nova-3" if lang == "en" else "nova-2"
    t0 = time.time()
    r = requests.post(f"https://api.deepgram.com/v1/listen?model={model}&language={lang}&smart_format=true",
                      headers={"Authorization": f"Token {_k('DEEPGRAM_API_KEY')}", "Content-Type": "audio/mpeg"},
                      data=audio, timeout=60)
    ms = int((time.time() - t0) * 1000)
    try: txt = r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
    except Exception: txt = ""
    return {"text": txt, "latency_ms": ms, "status": r.status_code, "mode": "sync"}


def stt_assemblyai(audio, lang):
    H = {"authorization": _k("ASSEMBLYAI_API_KEY")}
    t0 = time.time()
    up = requests.post("https://api.assemblyai.com/v2/upload", headers=H, data=audio, timeout=60).json()
    tid = requests.post("https://api.assemblyai.com/v2/transcript", headers=H,
                        json={"audio_url": up["upload_url"], "language_code": lang}, timeout=60).json()["id"]
    txt = ""; status = "timeout"
    for _ in range(25):
        time.sleep(1.2)
        r = requests.get(f"https://api.assemblyai.com/v2/transcript/{tid}", headers=H, timeout=60).json()
        if r.get("status") in ("completed", "error"):
            status = r.get("status"); txt = r.get("text") or ""; break
    return {"text": txt, "latency_ms": int((time.time() - t0) * 1000), "status": status, "mode": "batch (upload+poll)"}


def stt_elevenlabs(audio, lang):
    import io
    t0 = time.time()
    r = requests.post("https://api.elevenlabs.io/v1/speech-to-text",
                      headers={"xi-api-key": _k("ELEVENLABS_API_KEY")},
                      data={"model_id": "scribe_v1"},
                      files={"file": ("clip.mp3", io.BytesIO(audio), "audio/mpeg")}, timeout=60)
    ms = int((time.time() - t0) * 1000)
    try: txt = r.json().get("text", "")
    except Exception: txt = ""
    return {"text": txt, "latency_ms": ms, "status": r.status_code, "mode": "sync"}


STT_PROVIDERS = {"Atlas": stt_atlas, "ElevenLabs": stt_elevenlabs,
                 "Deepgram": stt_deepgram, "AssemblyAI": stt_assemblyai}


# ---- scoring ----
def wer(ref, hyp):
    norm = lambda s: re.sub(r"[^\w\s]", " ", s.lower()).split()
    r, h = norm(ref), norm(hyp)
    return _edit(r, h) / max(1, len(r))


def cer(ref, hyp):
    strip = lambda s: re.sub(r"[\s、。！？!?,.　]", "", s)
    r, h = strip(ref), strip(hyp)
    return _edit(list(r), list(h)) / max(1, len(r))


def _edit(r, h):
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1): d[i][0] = i
    for j in range(len(h) + 1): d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            c = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
    return d[len(r)][len(h)]
