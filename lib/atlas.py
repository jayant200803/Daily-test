"""Thin Atlas client built strictly from the Atlas docs."""
import os, time, json, requests
from config import BASE_URL, LISTEN_MODEL, SPEAK_MODEL

def _key():
    k = os.environ.get("ATLAS_API_KEY")
    if not k:
        raise SystemExit("ATLAS_API_KEY is not set. Copy .env.example to .env and add your key.")
    # Strip stray whitespace/newlines (common when pasting into a host's env-var box);
    # a newline in the key makes the Authorization header invalid.
    return k.strip()

def _auth():
    return {"Authorization": f"Bearer {_key()}"}

# ---- Listen: one-shot transcription  (POST /v1/audio/transcriptions) ----
def transcribe(path, language="en", prompt=None, content_type=None):
    files = {"file": (os.path.basename(path), open(path, "rb"), content_type)} if content_type \
            else {"file": (os.path.basename(path), open(path, "rb"))}
    data = {"model": LISTEN_MODEL, "language": language}
    if prompt:
        data["prompt"] = prompt
    t0 = time.time()
    r = requests.post(f"{BASE_URL}/v1/audio/transcriptions", headers=_auth(), data=data, files=files, timeout=120)
    ms = int((time.time() - t0) * 1000)
    out = {"status": r.status_code, "elapsed_ms": ms}
    try:
        out.update(r.json())
    except Exception:
        out["raw"] = r.text[:300]
    return out

# ---- Speak: synthesis  (POST /v1/audio/speech), streaming ----
def synthesize(text, voice, out_path, response_format="wav"):
    body = {"model": SPEAK_MODEL, "voice": voice, "input": text, "response_format": response_format}
    t0 = time.time()
    first_byte_ms = None
    total = 0
    with requests.post(f"{BASE_URL}/v1/audio/speech", headers={**_auth(), "Content-Type": "application/json"},
                       json=body, stream=True, timeout=120) as r:
        hdr_first = r.headers.get("X-Atlas-First-Audio-Ms")
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                if first_byte_ms is None:
                    first_byte_ms = int((time.time() - t0) * 1000)
                total += len(chunk)
                f.write(chunk)
        total_ms = int((time.time() - t0) * 1000)
    return {"status": r.status_code, "first_audio_header_ms": hdr_first,
            "first_byte_ms": first_byte_ms, "total_ms": total_ms, "bytes": total, "file": out_path}

def list_models():
    r = requests.get(f"{BASE_URL}/v1/models", headers=_auth(), timeout=30)
    return r.status_code, (r.json() if r.ok else r.text[:300])

# ---- Word Error Rate for accuracy scoring ----
def wer(reference, hypothesis):
    ref = reference.lower().split()
    hyp = hypothesis.lower().split()
    d = [[0]*(len(hyp)+1) for _ in range(len(ref)+1)]
    for i in range(len(ref)+1): d[i][0] = i
    for j in range(len(hyp)+1): d[0][j] = j
    for i in range(1, len(ref)+1):
        for j in range(1, len(hyp)+1):
            cost = 0 if ref[i-1] == hyp[j-1] else 1
            d[i][j] = min(d[i-1][j]+1, d[i][j-1]+1, d[i-1][j-1]+cost)
    errors = d[len(ref)][len(hyp)]
    return (errors / max(1, len(ref))), errors, len(ref)
