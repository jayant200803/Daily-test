import os, sys, json, threading, time
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from dotenv import load_dotenv; load_dotenv()
import config as C
from lib import atlas
from lib.phone import to_phone_wav

HERE = os.path.dirname(__file__)
AIN  = os.path.join(HERE, "audio_in")
AOUT = os.path.join(HERE, "audio_out")
os.makedirs(AOUT, exist_ok=True)
results = []

def line(): print("-"*78)
def rec(name, status, detail): results.append((name, status, detail)); print(f"[{status:>5}] {name}  —  {detail}")

def manifest():
    with open(os.path.join(HERE, "manifest.json")) as f: return json.load(f)

# ---------- LISTEN: accuracy / accents ----------
def listen_group(group, label):
    for item in manifest().get(group, []):
        path = os.path.join(AIN, item["file"])
        if not os.path.exists(path):
            rec(f"{label}: {item['file']}", "SKIP", "add this clip to audio_in/ (no file yet)"); continue
        out = atlas.transcribe(path, language=item.get("language","en"), prompt=item.get("prompt"))
        if out.get("status") != 200:
            rec(f"{label}: {item['file']}", "FAIL", f"HTTP {out.get('status')} {out.get('error',out.get('raw',''))}"); continue
        text = out.get("text","")
        exp  = item.get("expected")
        if exp:
            rate, errs, n = atlas.wer(exp, text)
            acc = round((1-rate)*100, 1)
            status = "PASS" if acc >= 85 else "CHECK"
            rec(f"{label}: {item['file']}", status, f"accuracy {acc}% ({errs}/{n} word errors) | got: '{text}'")
        else:
            rec(f"{label}: {item['file']}", "INFO", f"got: '{text}' (no expected text to score)")

# ---------- LISTEN: phone-quality ----------
def listen_phone():
    for item in manifest().get("phone", []):
        src = os.path.join(AIN, item["file"])
        if not os.path.exists(src):
            rec(f"phone: {item['file']}", "SKIP", "add this WAV clip to audio_in/"); continue
        try:
            dst = os.path.join(AOUT, "phone_8k_" + item["file"])
            to_phone_wav(src, dst)
        except Exception as e:
            rec(f"phone: {item['file']}", "FAIL", f"could not down-convert (needs a WAV source): {e}"); continue
        out = atlas.transcribe(dst, language=item.get("language","en"), prompt=item.get("prompt"))
        if out.get("status") != 200:
            rec(f"phone: {item['file']}", "FAIL", f"HTTP {out.get('status')} {out.get('error','')}"); continue
        text = out.get("text",""); exp = item.get("expected")
        if exp:
            rate, errs, n = atlas.wer(exp, text); acc = round((1-rate)*100,1)
            rec(f"phone: {item['file']}", "PASS" if acc>=80 else "CHECK",
                f"8kHz accuracy {acc}% | got: '{text}'")
        else:
            rec(f"phone: {item['file']}", "INFO", f"8kHz got: '{text}'")

# ---------- SPEAK: English quality (produces audio for manual listen) ----------
def speak_quality():
    for i, text in enumerate(C.ENGLISH_TEXTS, 1):
        out = atlas.synthesize(text, C.SPEAK_VOICE, os.path.join(AOUT, f"english_{i}.wav"))
        if out["status"] != 200:
            rec(f"speak english #{i}", "FAIL", f"HTTP {out['status']}"); continue
        rec(f"speak english #{i}", "LISTEN", f"{out['bytes']} bytes -> {os.path.basename(out['file'])} (open & judge quality)")

# ---------- SPEAK: streaming latency / time-to-first-audio ----------
def speak_latency():
    text = C.ENGLISH_TEXTS[0]
    out = atlas.synthesize(text, C.SPEAK_VOICE, os.path.join(AOUT, "latency.wav"), response_format="pcm")
    if out["status"] != 200:
        rec("speak latency", "FAIL", f"HTTP {out['status']}"); return
    hdr = out["first_audio_header_ms"]; fb = out["first_byte_ms"]
    target = C.FIRST_AUDIO_TARGET_MS
    detail = f"X-Atlas-First-Audio-Ms={hdr} | first byte {fb}ms | total {out['total_ms']}ms"
    if target and hdr is not None and str(hdr).isdigit():
        rec("speak latency (time to first audio)", "PASS" if int(hdr) <= target else "CHECK", detail + f" | target {target}ms")
    else:
        rec("speak latency (time to first audio)", "INFO", detail)

# ---------- SPEAK: pronunciation dictionary (key-based; manual listen) ----------
def speak_pron():
    if not C.PRON_WORDS:
        rec("pronunciation dictionary", "SKIP", "no PRON_WORDS set in config.py (dictionary lives on the key)"); return
    for w in C.PRON_WORDS:
        out = atlas.synthesize(w, C.SPEAK_VOICE, os.path.join(AOUT, f"pron_{w}.wav"))
        rec(f"pronunciation: {w}", "LISTEN", f"-> {os.path.basename(out['file'])} (confirm the reading matches the key's dictionary)")

# ---------- CONCURRENCY: N parallel, expect the (limit+1)th refused ----------
def concurrency():
    n = C.CONCURRENCY_ATTEMPT
    long_text = " ".join(C.ENGLISH_TEXTS * 6)
    codes = {}; lock = threading.Lock()
    import requests
    def worker(idx):
        body = {"model": C.SPEAK_MODEL, "voice": C.SPEAK_VOICE, "input": long_text, "response_format":"pcm"}
        try:
            with requests.post(f"{C.BASE_URL}/v1/audio/speech",
                               headers={"Authorization": f"Bearer {os.environ['ATLAS_API_KEY']}", "Content-Type":"application/json"},
                               json=body, stream=True, timeout=60) as r:
                code = r.status_code
                if code == 200:
                    next(r.iter_content(2048), None); time.sleep(2.5)   # hold the session open
                else:
                    code = f"{code}:{(r.json().get('error',{}) or {}).get('code','')}"
        except Exception as e:
            code = f"ERR:{e}"
        with lock: codes[idx] = code
    ths=[threading.Thread(target=worker,args=(i,)) for i in range(n)]
    for t in ths: t.start()
    for t in ths: t.join()
    vals=list(codes.values())
    refused=[v for v in vals if "session_limit" in str(v) or str(v).startswith("429")]
    ok=[v for v in vals if v==200]
    if refused:
        rec(f"concurrency ({n} parallel)", "PASS", f"{len(ok)} accepted, {len(refused)} refused with session_limit (429) as docs describe")
    else:
        rec(f"concurrency ({n} parallel)", "CHECK", f"all {len(ok)} accepted; key's limit may be >= {n}. codes={vals}")

def main():
    print("ATLAS DAY-1 TEST RUN"); line()
    sc, _ = atlas.list_models()
    rec("auth / connectivity (GET /v1/models)", "PASS" if sc==200 else "FAIL", f"HTTP {sc}")
    line(); print("LISTEN (speech to text)")
    listen_group("accuracy","accuracy"); listen_group("accents","accent"); listen_phone()
    line(); print("SPEAK (text to speech)")
    speak_quality(); speak_latency(); speak_pron()
    line(); print("CONCURRENCY")
    concurrency()
    line()
    p=sum(1 for _,s,_ in results if s=="PASS"); f=sum(1 for _,s,_ in results if s=="FAIL")
    print(f"SUMMARY: {p} pass, {f} fail, {len(results)} checks total.")

if __name__ == "__main__":
    main()
