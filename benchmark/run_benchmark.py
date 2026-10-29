"""Voice provider benchmark — TTS and STT, English + Japanese.

Sends the SAME input to every provider, measures real numbers, and writes a JSON
results file + saved audio samples. No numbers are invented; everything here is
measured live from the APIs using the keys in .env.

TTS compared:  Atlas (atlas-speak)  vs  ElevenLabs  vs  Cartesia
STT compared:  Atlas (atlas-listen) vs  Deepgram    vs  AssemblyAI

Run:  python benchmark/run_benchmark.py
"""
import os, sys, time, json, statistics, base64, io, wave, struct
import urllib.parse
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
from benchmark.sentences import ENGLISH, JAPANESE

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
os.makedirs(SAMPLES, exist_ok=True)

ATLAS = os.environ.get("ATLAS_API_KEY", "").strip()
EL = os.environ.get("ELEVENLABS_API_KEY", "").strip()
CART = os.environ.get("CARTESIA_API_KEY", "").strip()
DG = os.environ.get("DEEPGRAM_API_KEY", "").strip()
AAI = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()

TTS_RUNS = 3          # repeat each sentence this many times for latency (median/p95)
EL_VOICE = "JBFqnCBsd6RMkjVDRZzb"          # ElevenLabs public multilingual voice
CART_VOICE_EN = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"   # Skylar (en)
CART_VOICE_JA = "7ca2afba-a719-4f06-9af2-ea2b8e3cf14c"   # Yuto (ja)


# ---------------------------------------------------------------- TTS: measure first-byte
def _timed_stream_post(url, headers, json_body, out_path):
    """POST streaming; return (first_byte_ms, total_ms, bytes, status, extra_headers)."""
    t0 = time.time()
    first = None; total = 0
    with requests.post(url, headers=headers, json=json_body, stream=True, timeout=60) as r:
        hdrs = dict(r.headers)
        if r.status_code != 200:
            return None, None, 0, r.status_code, {"body": r.text[:200]}
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    if first is None:
                        first = int((time.time() - t0) * 1000)
                    total += len(chunk); f.write(chunk)
    total_ms = int((time.time() - t0) * 1000)
    return first, total_ms, total, 200, hdrs


def tts_atlas(text, lang, out_path):
    body = {"model": "atlas-speak", "voice": "souta", "input": text, "response_format": "mp3"}
    fb, tot, b, st, hd = _timed_stream_post(
        "https://atlas.oneinbox.ai/v1/audio/speech",
        {"Authorization": f"Bearer {ATLAS}", "Content-Type": "application/json"}, body, out_path)
    atlas_hdr = None
    if isinstance(hd, dict):
        atlas_hdr = hd.get("X-Atlas-First-Audio-Ms")
    return {"first_byte_ms": fb, "total_ms": tot, "bytes": b, "status": st,
            "provider_reported_first_audio_ms": (int(atlas_hdr) if atlas_hdr else None)}


def tts_elevenlabs(text, lang, out_path):
    model = "eleven_flash_v2_5" if lang == "en" else "eleven_turbo_v2_5"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE}/stream?output_format=mp3_44100_128"
    fb, tot, b, st, hd = _timed_stream_post(
        url, {"xi-api-key": EL, "Content-Type": "application/json"},
        {"text": text, "model_id": model}, out_path)
    return {"first_byte_ms": fb, "total_ms": tot, "bytes": b, "status": st, "model": model}


def tts_cartesia(text, lang, out_path):
    voice = CART_VOICE_EN if lang == "en" else CART_VOICE_JA
    body = {"model_id": "sonic-2", "transcript": text, "voice": {"mode": "id", "id": voice},
            "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000},
            "language": lang}
    fb, tot, b, st, hd = _timed_stream_post(
        "https://api.cartesia.ai/tts/bytes",
        {"X-API-Key": CART, "Cartesia-Version": "2024-11-13", "Content-Type": "application/json"},
        body, out_path)
    return {"first_byte_ms": fb, "total_ms": tot, "bytes": b, "status": st, "voice": voice}


TTS_PROVIDERS = {"Atlas": tts_atlas, "ElevenLabs": tts_elevenlabs, "Cartesia": tts_cartesia}


def run_tts():
    results = []
    for lang, sents in (("en", ENGLISH), ("ja", JAPANESE)):
        for name, fn in TTS_PROVIDERS.items():
            # warm up (not timed)
            try:
                fn(sents[0], lang, os.path.join(SAMPLES, f"_warm_{name}_{lang}.mp3"))
            except Exception:
                pass
            for si, text in enumerate(sents):
                fbs, tots = [], []
                last = {}
                for run in range(TTS_RUNS):
                    out = os.path.join(SAMPLES, f"tts_{name}_{lang}_s{si}.mp3")
                    try:
                        last = fn(text, lang, out)
                    except Exception as e:
                        last = {"status": "exc", "error": str(e)}
                    if last.get("status") == 200 and last.get("first_byte_ms") is not None:
                        fbs.append(last["first_byte_ms"]); tots.append(last["total_ms"])
                    time.sleep(0.3)
                ok = len(fbs) > 0
                results.append({
                    "type": "TTS", "provider": name, "language": lang, "sentence_index": si,
                    "sentence": text, "runs_ok": len(fbs), "runs_total": TTS_RUNS,
                    "first_byte_ms_median": int(statistics.median(fbs)) if ok else None,
                    "first_byte_ms_min": min(fbs) if ok else None,
                    "first_byte_ms_max": max(fbs) if ok else None,
                    "total_ms_median": int(statistics.median(tots)) if ok else None,
                    "audio_bytes": last.get("bytes"),
                    "provider_reported_first_audio_ms": last.get("provider_reported_first_audio_ms"),
                    "status": last.get("status"), "error": last.get("error"),
                })
                print(f"  TTS {name:11} {lang} s{si}: first-byte "
                      f"{results[-1]['first_byte_ms_median']}ms (median of {len(fbs)})  status={last.get('status')}")
    return results


# ---------------------------------------------------------------- STT: generate clips + accuracy
def gen_stt_clip(text, lang, out_path):
    """Generate a clean test clip with ElevenLabs (neutral to the STT engines compared:
    Atlas, Deepgram, AssemblyAI). Returns True on success."""
    model = "eleven_flash_v2_5" if lang == "en" else "eleven_turbo_v2_5"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE}?output_format=mp3_44100_128"
    r = requests.post(url, headers={"xi-api-key": EL, "Content-Type": "application/json"},
                      json={"text": text, "model_id": model}, timeout=60)
    if r.status_code == 200 and len(r.content) > 500:
        open(out_path, "wb").write(r.content); return True
    return False


def _norm_words(s):
    import re
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return s.split()


def wer(ref, hyp):
    r, h = _norm_words(ref), _norm_words(hyp)
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1): d[i][0] = i
    for j in range(len(h) + 1): d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            c = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
    return d[len(r)][len(h)] / max(1, len(r))


def cer(ref, hyp):
    import re
    strip = lambda s: re.sub(r"[\s、。！？!?,.　]", "", s)
    r, h = strip(ref), strip(hyp)
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1): d[i][0] = i
    for j in range(len(h) + 1): d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            c = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
    return d[len(r)][len(h)] / max(1, len(r))


def stt_atlas(path, lang):
    t0 = time.time()
    with open(path, "rb") as fh:
        r = requests.post("https://atlas.oneinbox.ai/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {ATLAS}"},
                          data={"model": "atlas-listen", "language": lang},
                          files={"file": ("clip.mp3", fh, "audio/mpeg")}, timeout=60)
    ms = int((time.time() - t0) * 1000)
    txt = ""
    try: txt = r.json().get("text", "")
    except Exception: pass
    return {"text": txt, "latency_ms": ms, "status": r.status_code, "mode": "sync"}


def stt_deepgram(path, lang):
    model = "nova-3" if lang == "en" else "nova-2"   # nova-2 covers Japanese
    t0 = time.time()
    with open(path, "rb") as fh:
        r = requests.post(f"https://api.deepgram.com/v1/listen?model={model}&language={lang}&smart_format=true",
                          headers={"Authorization": f"Token {DG}", "Content-Type": "audio/mpeg"},
                          data=fh.read(), timeout=60)
    ms = int((time.time() - t0) * 1000)
    txt = ""
    try: txt = r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
    except Exception: pass
    return {"text": txt, "latency_ms": ms, "status": r.status_code, "mode": "sync", "model": model}


def stt_assemblyai(path, lang):
    H = {"authorization": AAI}
    t0 = time.time()
    up = requests.post("https://api.assemblyai.com/v2/upload", headers=H, data=open(path, "rb").read()).json()
    tid = requests.post("https://api.assemblyai.com/v2/transcript", headers=H,
                        json={"audio_url": up["upload_url"], "language_code": lang}).json()["id"]
    txt = ""; status = "timeout"
    for _ in range(40):
        time.sleep(1.5)
        r = requests.get(f"https://api.assemblyai.com/v2/transcript/{tid}", headers=H).json()
        if r.get("status") in ("completed", "error"):
            status = r.get("status"); txt = r.get("text") or ""
            break
    ms = int((time.time() - t0) * 1000)
    return {"text": txt, "latency_ms": ms, "status": status, "mode": "batch (upload+poll)"}


STT_PROVIDERS = {"Atlas": stt_atlas, "Deepgram": stt_deepgram, "AssemblyAI": stt_assemblyai}


def run_stt():
    results = []
    for lang, sents in (("en", ENGLISH), ("ja", JAPANESE)):
        for si, text in enumerate(sents):
            clip = os.path.join(SAMPLES, f"stt_input_{lang}_s{si}.mp3")
            if not os.path.exists(clip):
                if not gen_stt_clip(text, lang, clip):
                    print(f"  (could not generate {lang} clip s{si}, skipping)"); continue
            for name, fn in STT_PROVIDERS.items():
                try:
                    res = fn(clip, lang)
                except Exception as e:
                    res = {"text": "", "latency_ms": None, "status": "exc", "error": str(e), "mode": "?"}
                score = cer(text, res["text"]) if lang == "ja" else wer(text, res["text"])
                acc = round((1 - score) * 100, 1)
                results.append({
                    "type": "STT", "provider": name, "language": lang, "sentence_index": si,
                    "reference": text, "transcript": res.get("text"),
                    "error_rate": round(score, 3),
                    "accuracy_pct": acc, "metric": ("CER" if lang == "ja" else "WER"),
                    "latency_ms": res.get("latency_ms"), "mode": res.get("mode"),
                    "status": res.get("status"),
                })
                print(f"  STT {name:11} {lang} s{si}: acc {acc}%  "
                      f"({results[-1]['metric']})  {res.get('latency_ms')}ms  [{res.get('mode')}]")
    return results


def main():
    print("Keys present:",
          {"atlas": bool(ATLAS), "elevenlabs": bool(EL), "cartesia": bool(CART),
           "deepgram": bool(DG), "assemblyai": bool(AAI)})
    print("\n=== TTS benchmark (first-byte latency) ===")
    tts = run_tts()
    print("\n=== STT benchmark (accuracy + latency) ===")
    stt = run_stt()
    out = {"generated_at_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
           "tts_runs_per_sentence": TTS_RUNS, "tts": tts, "stt": stt}
    path = os.path.join(HERE, "results.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nSaved results -> {path}")
    print(f"Audio samples -> {SAMPLES}")


if __name__ == "__main__":
    main()
