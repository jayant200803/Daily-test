"""Local verification page for the benchmark.

Lets your team re-run the SAME tests live and confirm the Excel numbers:
- TTS: type text -> all 3 providers speak it -> see first-byte latency + play each audio.
- STT: type text -> we make one clip -> all 3 STT transcribe it -> see accuracy + latency.

It uses the EXACT same measurement code as run_benchmark.py and the keys from .env,
so the numbers line up with the spreadsheet. Runs on your machine; keys stay local.

Run:  python benchmark/verify_app.py   ->  open http://127.0.0.1:5001
"""
import os, sys, time
from flask import Flask, request, jsonify, send_from_directory, render_template_string

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmark import run_benchmark as B   # reuse the same measurement functions

app = Flask(__name__)
SAMPLES = B.SAMPLES

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Benchmark Verifier</title>
<style>
 :root{--bg:#0f1419;--card:#1a222c;--fg:#e6edf3;--muted:#8b98a5;--accent:#2f81f7;--ok:#2ea043;--border:#30363d;}
 body{margin:0;font:16px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--fg);}
 .wrap{max-width:900px;margin:0 auto;padding:24px 16px 64px;}
 h1{font-size:22px;margin:0 0 4px;} .sub{color:var(--muted);font-size:14px;margin:0 0 20px;}
 .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:20px;}
 h2{font-size:17px;margin:0 0 12px;} label{display:block;font-size:13px;color:var(--muted);margin:10px 0 4px;}
 input,select,textarea{width:100%;background:#0d1117;color:var(--fg);border:1px solid var(--border);border-radius:8px;padding:9px 11px;font:inherit;}
 button{cursor:pointer;border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:600;color:#fff;background:var(--accent);margin-top:12px;}
 button:disabled{opacity:.5;}
 table{width:100%;border-collapse:collapse;margin-top:14px;font-size:14px;}
 th,td{border:1px solid var(--border);padding:8px 10px;text-align:left;vertical-align:top;}
 th{background:#0d1117;color:var(--muted);} .best{color:var(--ok);font-weight:700;}
 audio{width:220px;height:34px;} .meta{color:var(--muted);font-size:12px;} .err{color:#e5534b;}
</style></head><body><div class="wrap">
 <h1>Benchmark Verifier</h1>
 <p class="sub">Re-run the comparisons live and confirm the numbers. Same measurement code as the Excel; keys stay on this machine.</p>

 <div class="card">
  <h2>🔊 TTS — speed comparison (Atlas vs ElevenLabs vs Cartesia)</h2>
  <label>Text to speak</label>
  <textarea id="ttsText">Thank you for calling Manulife Finance, how may I help you today?</textarea>
  <label>Language</label>
  <select id="ttsLang"><option value="en" selected>English</option><option value="ja">Japanese</option></select>
  <button id="ttsBtn">Compare TTS</button>
  <div id="ttsOut"></div>
 </div>

 <div class="card">
  <h2>📝 STT — accuracy comparison (Atlas vs Deepgram vs AssemblyAI)</h2>
  <p class="meta">We generate one clean clip of this text, then every provider transcribes the SAME clip. Accuracy compares the transcript to the text below.</p>
  <label>Reference text (the correct words)</label>
  <textarea id="sttText">I would like to transfer five thousand dollars to my savings account.</textarea>
  <label>Language</label>
  <select id="sttLang"><option value="en" selected>English (WER)</option><option value="ja">Japanese (CER)</option></select>
  <button id="sttBtn">Compare STT</button>
  <div id="sttOut"></div>
 </div>
</div>
<script>
const el=id=>document.getElementById(id);
el('ttsBtn').onclick=async()=>{
 el('ttsBtn').disabled=true; el('ttsOut').innerHTML='<p class="meta">measuring…</p>';
 try{
  const r=await fetch('/api/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:el('ttsText').value,lang:el('ttsLang').value})});
  const j=await r.json();
  const best=Math.min(...j.rows.filter(x=>x.first_byte_ms).map(x=>x.first_byte_ms));
  let h='<table><tr><th>Provider</th><th>First-byte latency</th><th>Total time</th><th>Listen</th></tr>';
  for(const x of j.rows){
    const fb=x.first_byte_ms!=null?(x.first_byte_ms+' ms'):('<span class=err>'+(x.status)+'</span>');
    const cls=x.first_byte_ms===best?'best':'';
    h+=`<tr><td>${x.provider}</td><td class="${cls}">${fb}</td><td>${x.total_ms!=null?x.total_ms+' ms':'-'}</td><td>${x.audio?`<audio controls src="${x.audio}"></audio>`:'-'}</td></tr>`;
  }
  h+='</table><p class="meta">Lower first-byte = voice starts sooner. Green = fastest.</p>';
  el('ttsOut').innerHTML=h;
 }catch(e){el('ttsOut').innerHTML='<p class="err">'+e.message+'</p>';}
 el('ttsBtn').disabled=false;
};
el('sttBtn').onclick=async()=>{
 el('sttBtn').disabled=true; el('sttOut').innerHTML='<p class="meta">generating clip + transcribing… (AssemblyAI takes a few seconds)</p>';
 try{
  const r=await fetch('/api/stt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:el('sttText').value,lang:el('sttLang').value})});
  const j=await r.json();
  if(j.error){el('sttOut').innerHTML='<p class="err">'+j.error+'</p>';el('sttBtn').disabled=false;return;}
  const best=Math.max(...j.rows.map(x=>x.accuracy));
  let h=`<p class="meta">Test clip: <audio controls src="${j.clip}"></audio> &nbsp; metric: <b>${j.metric}</b></p>`;
  h+='<table><tr><th>Provider</th><th>Accuracy</th><th>Latency</th><th>Mode</th><th>What it heard</th></tr>';
  for(const x of j.rows){
    const cls=x.accuracy===best?'best':'';
    h+=`<tr><td>${x.provider}</td><td class="${cls}">${x.accuracy}%</td><td>${x.latency_ms!=null?x.latency_ms+' ms':'-'}</td><td class="meta">${x.mode}</td><td>${escapeHtml(x.transcript||'')}</td></tr>`;
  }
  h+='</table><p class="meta">Higher accuracy = better. Green = best. AssemblyAI runs in batch mode so compare its accuracy, not its latency.</p>';
  el('sttOut').innerHTML=h;
 }catch(e){el('sttOut').innerHTML='<p class="err">'+e.message+'</p>';}
 el('sttBtn').disabled=false;
};
function escapeHtml(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
</script></body></html>"""


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/audio/<path:name>")
def audio(name):
    return send_from_directory(SAMPLES, name)


@app.post("/api/tts")
def api_tts():
    d = request.get_json(silent=True) or {}
    text = (d.get("text") or "").strip(); lang = d.get("lang", "en")
    rows = []
    for name, fn in B.TTS_PROVIDERS.items():
        fname = f"verify_tts_{name}_{lang}.mp3"
        out = os.path.join(SAMPLES, fname)
        try:
            res = fn(text, lang, out)
        except Exception as e:
            res = {"status": "exc", "error": str(e)}
        rows.append({"provider": name, "first_byte_ms": res.get("first_byte_ms"),
                     "total_ms": res.get("total_ms"), "status": res.get("status"),
                     "audio": ("/audio/" + fname) if res.get("status") == 200 else None})
    return jsonify(rows=rows)


@app.post("/api/stt")
def api_stt():
    d = request.get_json(silent=True) or {}
    text = (d.get("text") or "").strip(); lang = d.get("lang", "en")
    clip_name = f"verify_stt_{lang}.mp3"
    clip = os.path.join(SAMPLES, clip_name)
    if not B.gen_stt_clip(text, lang, clip):
        return jsonify(error="Could not generate the test clip (check ElevenLabs key/credits).")
    rows = []
    for name, fn in B.STT_PROVIDERS.items():
        try:
            res = fn(clip, lang)
        except Exception as e:
            res = {"text": "", "latency_ms": None, "status": "exc", "mode": "?", "error": str(e)}
        score = B.cer(text, res.get("text") or "") if lang == "ja" else B.wer(text, res.get("text") or "")
        rows.append({"provider": name, "transcript": res.get("text"),
                     "accuracy": round((1 - score) * 100, 1), "latency_ms": res.get("latency_ms"),
                     "mode": res.get("mode")})
    return jsonify(clip="/audio/" + clip_name, metric=("CER" if lang == "ja" else "WER"), rows=rows)


if __name__ == "__main__":
    print("=" * 60)
    print("BENCHMARK VERIFIER")
    print("  open http://127.0.0.1:5001")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5001, debug=False)
