"""Voice provider benchmark — measures real numbers for the Excel.

TTS: first-byte + processing latency (network removed) for Atlas, ElevenLabs, Cartesia, Deepgram.
STT: streaming time-to-first-word (Atlas, Deepgram) and batch (AssemblyAI, ElevenLabs),
     plus accuracy (WER English / CER Japanese).

Nothing is invented — everything is measured live from the APIs. Run:
    python benchmark/run_benchmark.py       (then: python benchmark/make_excel.py)
"""
import os, sys, time, json, io, wave, statistics, base64
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
from benchmark.sentences import ENGLISH, JAPANESE
from benchmark import providers as P
from benchmark import streaming as S

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
os.makedirs(SAMPLES, exist_ok=True)
TTS_RUNS = 3
STREAMING_STT = {"Atlas", "Deepgram"}


def _wav_clip(text, lang):
    """Generate a clean WAV (16kHz mono PCM) test clip with ElevenLabs (neutral to the STT
    engines). WAV so the realtime streaming APIs can use it."""
    model = "eleven_flash_v2_5" if lang == "en" else "eleven_turbo_v2_5"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{P.EL_VOICE}?output_format=pcm_16000"
    r = requests.post(url, headers={"xi-api-key": P._k("ELEVENLABS_API_KEY"), "Content-Type": "application/json"},
                      json={"text": text, "model_id": model}, timeout=60)
    if r.status_code != 200 or len(r.content) < 500:
        return None
    buf = io.BytesIO()
    w = wave.open(buf, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(r.content); w.close()
    return buf.getvalue()


def run_tts():
    results = []
    for lang, sents in (("en", ENGLISH), ("ja", JAPANESE)):
        for name, fn in P.TTS_PROVIDERS.items():
            try: fn(sents[0], lang)   # warm up
            except Exception: pass
            for si, text in enumerate(sents):
                procs, fbs, tots = [], [], []
                last = {}
                for _ in range(TTS_RUNS):
                    try: last = fn(text, lang)
                    except Exception as e: last = {"status": f"exc:{e}"}
                    if last.get("status") == 200 and last.get("processing_ms") is not None:
                        procs.append(last["processing_ms"]); fbs.append(last["first_byte_ms"]); tots.append(last["total_ms"])
                    time.sleep(0.3)
                if last.get("status") == 200 and last.get("audio"):
                    open(os.path.join(SAMPLES, f"tts_{name}_{lang}_s{si}.mp3"), "wb").write(last["audio"])
                ok = len(procs) > 0
                results.append({"provider": name, "language": lang, "sentence_index": si, "sentence": text,
                                "processing_ms_median": int(statistics.median(procs)) if ok else None,
                                "first_byte_ms_median": int(statistics.median(fbs)) if ok else None,
                                "total_ms_median": int(statistics.median(tots)) if ok else None,
                                "runs_ok": len(procs), "runs_total": TTS_RUNS, "status": last.get("status")})
                print(f"  TTS {name:11} {lang} s{si}: processing {results[-1]['processing_ms_median']}ms  "
                      f"(first-byte {results[-1]['first_byte_ms_median']}ms)  status={last.get('status')}")
    return results


def run_stt():
    results = []
    clips = {}
    for lang, sents in (("en", ENGLISH), ("ja", JAPANESE)):
        for si, text in enumerate(sents):
            wav = _wav_clip(text, lang)
            if not wav:
                print(f"  (clip gen failed {lang} s{si})"); continue
            clips[(lang, si)] = wav
            for name in P.STT_PROVIDERS:
                if name in STREAMING_STT:
                    r = S.streaming_ttfp(name, wav, lang)
                    txt, lat, mode = r.get("transcript") or "", r.get("ttfp_ms"), "streaming (time to first word)"
                else:
                    try: rr = P.STT_PROVIDERS[name](wav, lang)
                    except Exception as e: rr = {"text": "", "latency_ms": None, "mode": f"exc:{e}"}
                    txt, lat, mode = rr.get("text") or "", rr.get("latency_ms"), rr.get("mode")
                score = P.cer(text, txt) if lang == "ja" else P.wer(text, txt)
                results.append({"provider": name, "language": lang, "sentence_index": si, "reference": text,
                                "transcript": txt, "accuracy_pct": round((1 - score) * 100, 1),
                                "metric": ("CER" if lang == "ja" else "WER"), "latency_ms": lat, "mode": mode})
                print(f"  STT {name:11} {lang} s{si}: acc {results[-1]['accuracy_pct']}%  {lat}ms  [{mode}]")
    return results


def main():
    print("=== TTS (processing latency, network removed) ===")
    tts = run_tts()
    print("\n=== STT (streaming TTFP + accuracy) ===")
    stt = run_stt()
    out = {"generated_at_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
           "tts_runs_per_sentence": TTS_RUNS, "tts": tts, "stt": stt}
    json.dump(out, open(os.path.join(HERE, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\nSaved results -> benchmark/results.json")


if __name__ == "__main__":
    main()
