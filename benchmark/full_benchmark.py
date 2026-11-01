"""Full voice benchmark -> results_full.json + audio samples for the report.

TTS (Atlas, ElevenLabs, Cartesia, Deepgram): time-to-first-audio (TTFA, network removed),
full generation time, RTF, naturalness (HNR 1-5), prosody (pitch std), correctness WER,
and robustness (WER on hard-case sentences).
STT (Atlas, ElevenLabs, Deepgram, AssemblyAI): streaming time-to-first-word + accuracy.

All numbers are measured live. Run: python benchmark/full_benchmark.py
"""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
from benchmark.dataset import DATASET
from benchmark import providers as P
from benchmark import quality as Q
from benchmark import streaming as S

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_SAMPLES = os.path.join(HERE, "site", "samples")
os.makedirs(SITE_SAMPLES, exist_ok=True)
STREAMING_STT = {"Atlas", "Deepgram"}


def run_tts():
    rows = []
    for lang, items in DATASET.items():
        for name in P.TTS_PROVIDERS:
            # warm up
            try: P.TTS_PROVIDERS[name](items[0]["text"], lang)
            except Exception: pass
            for it in items:
                text, cat, sid = it["text"], it["category"], it["id"]
                # 1) TTFA (streaming first byte, network removed)
                try: tf = P.TTS_PROVIDERS[name](text, lang)
                except Exception as e: tf = {"status": f"exc:{e}"}
                ttfa = tf.get("processing_ms")
                # 2) full WAV (generation time + audio for analysis)
                try: gen_ms, wav, st = P.TTS_WAV_PROVIDERS[name](text, lang)
                except Exception as e: gen_ms, wav, st = None, b"", f"exc:{e}"
                dur = Q.wav_duration_sec(wav) if wav else None
                rtf = round((gen_ms / 1000.0) / dur, 3) if (gen_ms and dur) else None
                hnr = Q.hnr_mean(wav) if wav else None
                nat = Q.naturalness_score(hnr)
                pros = Q.pitch_std(wav) if wav else None
                wer, hyp = (Q.correctness(wav, text, lang) if wav else (None, ""))
                if wav and cat == "general":
                    open(os.path.join(SITE_SAMPLES, f"tts_{name}_{lang}_{sid}.wav"), "wb").write(wav)
                rows.append({"provider": name, "language": lang, "id": sid, "category": cat, "text": text,
                             "ttfa_ms": ttfa, "first_byte_ms": tf.get("first_byte_ms"),
                             "atlas_reported_ttfa_ms": tf.get("provider_reported_first_audio_ms"),
                             "gen_time_ms": gen_ms, "audio_sec": round(dur, 2) if dur else None, "rtf": rtf,
                             "naturalness": nat, "prosody_hz": pros, "wer": wer, "transcript": hyp,
                             "tts_status": st})
                print(f"  TTS {name:11} {lang} {sid:10} ttfa={ttfa}ms rtf={rtf} nat={nat} wer={wer}")
                time.sleep(0.2)
    return rows


def run_stt():
    rows = []
    for lang, items in DATASET.items():
        for it in items:
            text, sid, cat = it["text"], it["id"], it["category"]
            wav = _wav_clip(text, lang)
            if not wav:
                print(f"  (clip gen failed {sid})"); continue
            for name in P.STT_PROVIDERS:
                if name in STREAMING_STT:
                    r = S.streaming_ttfp(name, wav, lang)
                    txt, lat, mode = r.get("transcript") or "", r.get("ttfp_ms"), "streaming (time to first word)"
                else:
                    try: rr = P.STT_PROVIDERS[name](wav, lang)
                    except Exception as e: rr = {"text": "", "latency_ms": None, "mode": f"exc:{e}"}
                    txt, lat, mode = rr.get("text") or "", rr.get("latency_ms"), rr.get("mode")
                score = P.cer(text, txt) if lang == "ja" else P.wer(text, txt)
                rows.append({"provider": name, "language": lang, "id": sid, "category": cat, "reference": text,
                             "transcript": txt, "wer": round(score, 3), "accuracy_pct": round((1 - score) * 100, 1),
                             "metric": ("CER" if lang == "ja" else "WER"), "latency_ms": lat, "mode": mode})
                print(f"  STT {name:11} {lang} {sid:10} acc={rows[-1]['accuracy_pct']}% {lat}ms [{mode}]")
    return rows


def _wav_clip(text, lang):
    import requests, io, wave
    model = "eleven_flash_v2_5" if lang == "en" else "eleven_turbo_v2_5"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{P.EL_VOICE}?output_format=pcm_16000"
    r = requests.post(url, headers={"xi-api-key": P._k("ELEVENLABS_API_KEY"), "Content-Type": "application/json"},
                      json={"text": text, "model_id": model}, timeout=60)
    if r.status_code != 200 or len(r.content) < 500:
        return None
    b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(r.content); w.close()
    return b.getvalue()


def main():
    print("=== TTS ===")
    tts = run_tts()
    print("\n=== STT ===")
    stt = run_stt()
    out = {"generated_at_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "tts": tts, "stt": stt}
    json.dump(out, open(os.path.join(HERE, "results_full.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\nSaved -> benchmark/results_full.json  | samples -> benchmark/site/samples/")


if __name__ == "__main__":
    main()
