"""Atlas live web demo — open in a browser, click to speak (STT) or type (TTS).

Your team opens one URL. No Python on their side. The Atlas test key stays
HERE on the server (loaded from .env), never shipped to the browser.

Endpoints hit Atlas exactly per the docs, via lib/atlas.py:
  POST /api/stt  -> atlas-listen  (/v1/audio/transcriptions)
  POST /api/tts  -> atlas-speak   (/v1/audio/speech)

Run:   python webapp.py
Then open http://127.0.0.1:5000  (or the LAN address it prints) in a browser.
"""
import os, tempfile
from flask import Flask, request, jsonify, send_file, Response, render_template_string

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import SPEAK_VOICE
from lib import atlas

app = Flask(__name__)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atlas Live Test</title>
<style>
  :root { --bg:#0f1419; --card:#1a222c; --fg:#e6edf3; --muted:#8b98a5;
          --accent:#2f81f7; --ok:#2ea043; --rec:#e5534b; --border:#30363d; }
  * { box-sizing:border-box; }
  body { margin:0; font:16px/1.5 system-ui,Segoe UI,sans-serif; background:var(--bg); color:var(--fg); }
  .wrap { max-width:720px; margin:0 auto; padding:24px 16px 64px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 24px; font-size:14px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; margin-bottom:20px; }
  .card h2 { font-size:16px; margin:0 0 14px; }
  label { display:block; font-size:13px; color:var(--muted); margin:12px 0 4px; }
  input[type=text], input[type=number], select, textarea {
    width:100%; background:#0d1117; color:var(--fg); border:1px solid var(--border);
    border-radius:8px; padding:9px 11px; font:inherit; }
  textarea { resize:vertical; min-height:70px; }
  button { cursor:pointer; border:0; border-radius:8px; padding:10px 18px; font:inherit;
    font-weight:600; color:#fff; background:var(--accent); }
  button:disabled { opacity:.5; cursor:default; }
  button.rec { background:var(--rec); }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .result { margin-top:14px; padding:12px 14px; border-radius:8px; background:#0d1117;
    border:1px solid var(--border); min-height:20px; white-space:pre-wrap; }
  .heard { font-size:18px; color:var(--fg); }
  .meta { color:var(--muted); font-size:12px; margin-top:6px; }
  .err { color:var(--rec); }
  .dot { width:10px; height:10px; border-radius:50%; background:var(--rec); display:inline-block;
    margin-right:6px; animation:p 1s infinite; }
  @keyframes p { 50% { opacity:.3; } }
</style>
</head>
<body>
<div class="wrap">
  <h1>Atlas Live Test</h1>
  <p class="sub">Speak to test <b>atlas-listen</b> (STT). Type to test <b>atlas-speak</b> (TTS). Endpoints are exactly per the Atlas docs; nothing is scored.</p>

  <div class="card">
    <h2>🎙️ Speak &rarr; atlas-listen (STT)</h2>
    <div class="row">
      <button id="recBtn">Start recording</button>
      <span id="recStatus" class="meta"></span>
    </div>
    <label>Language</label>
    <select id="lang"><option value="en" selected>en (English)</option><option value="ja">ja (Japanese)</option></select>
    <label>Vocabulary hint (optional, comma-separated words)</label>
    <input type="text" id="prompt" placeholder="e.g. OneInbox, Exotel, NS1156">
    <div class="result" id="sttOut"><span class="meta">Transcript will appear here.</span></div>
  </div>

  <div class="card">
    <h2>⌨️ Type &rarr; atlas-speak (TTS)</h2>
    <label>Text to speak</label>
    <textarea id="ttsText">Thank you for calling. How may I help you with your booking today?</textarea>
    <label>Voice (12 Japanese voices per the docs; default souta)</label>
    <select id="voice">
      <option value="ayaka">ayaka (あやか)</option>
      <option value="emi">emi (えみ)</option>
      <option value="kaito">kaito (かいと)</option>
      <option value="sakura">sakura (さくら)</option>
      <option value="souta" selected>souta (そうた) — default</option>
      <option value="nanami">nanami (ななみ)</option>
      <option value="haruto">haruto (はると)</option>
      <option value="mei">mei (めい)</option>
      <option value="yuto">yuto (ゆうと)</option>
      <option value="rina">rina (りな)</option>
      <option value="ren">ren (れん)</option>
      <option value="hajun">hajun (ハジュン)</option>
    </select>
    <div class="row" style="margin-top:12px">
      <button id="speakBtn">Speak it</button>
      <span id="ttsStatus" class="meta"></span>
    </div>
    <audio id="player" controls style="width:100%; margin-top:12px; display:none"></audio>
    <div class="result" id="ttsOut" style="display:none"></div>
  </div>
</div>

<script>
// ---------- STT: capture mic as 16-bit PCM mono WAV (Atlas-accepted) ----------
let recording = false, audioCtx, stream, procNode, srcNode, buffers = [], sr = 16000;
let recTimer = null, recElapsed = 0;
const MAX_SECONDS = 90;   // 1:30 cap
const recBtn = document.getElementById('recBtn');
const recStatus = document.getElementById('recStatus');
const sttOut = document.getElementById('sttOut');

recBtn.onclick = async () => {
  if (!recording) { await startRec(); } else { await stopRec(); }
};

async function startRec() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) { sttOut.innerHTML = '<span class="err">Microphone blocked: '+e.message+'</span>'; return; }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  sr = audioCtx.sampleRate;
  srcNode = audioCtx.createMediaStreamSource(stream);
  procNode = audioCtx.createScriptProcessor(4096, 1, 1);
  buffers = [];
  procNode.onaudioprocess = e => buffers.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  srcNode.connect(procNode); procNode.connect(audioCtx.destination);
  recording = true;
  recBtn.textContent = 'Stop & transcribe'; recBtn.classList.add('rec');
  recElapsed = 0;
  const fmt = s => (Math.floor(s/60))+':'+String(s%60).padStart(2,'0');
  const paint = () => { recStatus.innerHTML = '<span class="dot"></span>recording '+fmt(recElapsed)+' / '+fmt(MAX_SECONDS); };
  paint();
  recTimer = setInterval(() => {
    recElapsed++; paint();
    if (recElapsed >= MAX_SECONDS) { stopRec(); }   // auto-stop at 1:30
  }, 1000);
}

async function stopRec() {
  if (!recording) return;          // guard: timer + click can both fire
  recording = false;
  if (recTimer) { clearInterval(recTimer); recTimer = null; }
  procNode.disconnect(); srcNode.disconnect();
  stream.getTracks().forEach(t => t.stop());
  await audioCtx.close();
  recBtn.textContent = 'Start recording'; recBtn.classList.remove('rec');
  recStatus.textContent = 'uploading…';

  const wav = encodeWav(flatten(buffers), sr);
  const fd = new FormData();
  fd.append('file', new Blob([wav], {type:'audio/wav'}), 'mic.wav');
  fd.append('language', document.getElementById('lang').value);
  fd.append('prompt', document.getElementById('prompt').value);

  try {
    const r = await fetch('/api/stt', { method:'POST', body: fd });
    const j = await r.json();
    recStatus.textContent = '';
    if (j.ok) {
      sttOut.innerHTML = '<div class="heard">“'+ (j.text||'').trim() +'”</div>' +
        '<div class="meta">status '+j.status+' · '+(j.elapsed_ms||'?')+' ms</div>';
    } else {
      sttOut.innerHTML = '<span class="err">Error '+(j.status||'')+': '+(j.detail||j.error||'failed')+'</span>';
    }
  } catch (e) { recStatus.textContent=''; sttOut.innerHTML = '<span class="err">'+e.message+'</span>'; }
}

function flatten(bufs){ let len=0; bufs.forEach(b=>len+=b.length); const out=new Float32Array(len); let o=0;
  bufs.forEach(b=>{ out.set(b,o); o+=b.length; }); return out; }

function encodeWav(samples, rate){
  const buf = new ArrayBuffer(44 + samples.length*2); const v = new DataView(buf);
  const ws=(off,s)=>{ for(let i=0;i<s.length;i++) v.setUint8(off+i, s.charCodeAt(i)); };
  ws(0,'RIFF'); v.setUint32(4,36+samples.length*2,true); ws(8,'WAVE'); ws(12,'fmt ');
  v.setUint32(16,16,true); v.setUint16(20,1,true); v.setUint16(22,1,true);
  v.setUint32(24,rate,true); v.setUint32(28,rate*2,true); v.setUint16(32,2,true);
  v.setUint16(34,16,true); ws(36,'data'); v.setUint32(40,samples.length*2,true);
  let off=44; for(let i=0;i<samples.length;i++){ let s=Math.max(-1,Math.min(1,samples[i]));
    v.setInt16(off, s<0 ? s*0x8000 : s*0x7FFF, true); off+=2; }
  return buf;
}

// ---------- TTS ----------
const speakBtn = document.getElementById('speakBtn');
const ttsStatus = document.getElementById('ttsStatus');
const player = document.getElementById('player');
const ttsOut = document.getElementById('ttsOut');

speakBtn.onclick = async () => {
  const text = document.getElementById('ttsText').value.trim();
  if (!text) return;
  speakBtn.disabled = true; ttsStatus.textContent = 'synthesizing…';
  ttsOut.style.display='none';
  try {
    const r = await fetch('/api/tts', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ text, voice: document.getElementById('voice').value }) });
    if (!r.ok) { const j = await r.json().catch(()=>({})); throw new Error((j.detail||j.error||('HTTP '+r.status))); }
    const firstMs = r.headers.get('X-Atlas-First-Audio-Ms');
    const blob = await r.blob();
    player.src = URL.createObjectURL(blob); player.style.display='block'; player.play();
    ttsStatus.textContent = '';
    ttsOut.style.display='block';
    ttsOut.innerHTML = '<span class="meta">ok · '+blob.size+' bytes' + (firstMs ? ' · first-audio '+firstMs+' ms' : '') + '</span>';
  } catch (e) {
    ttsStatus.textContent=''; ttsOut.style.display='block';
    ttsOut.innerHTML = '<span class="err">'+e.message+'</span>';
  } finally { speakBtn.disabled = false; }
};
</script>
</body>
</html>"""


def _key_missing():
    """Return a JSON 500 if the server has no Atlas key, else None."""
    if not os.environ.get("ATLAS_API_KEY"):
        return jsonify(ok=False, error="ATLAS_API_KEY is not set on the server. "
                       "On Vercel: Project > Settings > Environment Variables, add "
                       "ATLAS_API_KEY, then redeploy."), 500
    return None


# Any uncaught error returns JSON (not an HTML page), so the browser shows the real reason.
@app.errorhandler(Exception)
def _json_errors(e):
    from werkzeug.exceptions import HTTPException
    code = e.code if isinstance(e, HTTPException) else 500
    return jsonify(ok=False, error=type(e).__name__, detail=str(e)), code


@app.get("/")
def index():
    return render_template_string(PAGE.replace("__VOICE__", SPEAK_VOICE))


@app.get("/api/health")
def api_health():
    """Quick check: is the key set and can we reach Atlas? Returns JSON."""
    if not os.environ.get("ATLAS_API_KEY"):
        return jsonify(ok=False, key_set=False, error="ATLAS_API_KEY not set on server"), 500
    try:
        code, _ = atlas.list_models()
        return jsonify(ok=code == 200, key_set=True, models_status=code)
    except Exception as e:
        return jsonify(ok=False, key_set=True, error=type(e).__name__, detail=str(e)), 502


@app.post("/api/stt")
def api_stt():
    missing = _key_missing()
    if missing:
        return missing
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="no audio file"), 400
    language = request.form.get("language", "en") or "en"
    prompt = (request.form.get("prompt") or "").strip() or None
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        f.save(tmp.name)
        tmp.close()
        res = atlas.transcribe(tmp.name, language=language, prompt=prompt, content_type="audio/wav")
        ok = res.get("status") == 200 and "text" in res
        return jsonify(ok=ok, status=res.get("status"), text=res.get("text"),
                       elapsed_ms=res.get("elapsed_ms"), detail=res.get("raw"))
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass


@app.post("/api/tts")
def api_tts():
    missing = _key_missing()
    if missing:
        return missing
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="no text"), 400
    voice = (data.get("voice") or SPEAK_VOICE).strip()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    res = atlas.synthesize(text, voice=voice, out_path=tmp.name, response_format="wav")
    if res.get("status") != 200 or not res.get("bytes"):
        detail = res.get("raw") or res.get("error")
        try: os.unlink(tmp.name)
        except OSError: pass
        return jsonify(ok=False, error=f"atlas-speak returned {res.get('status')}", detail=detail), 502
    resp = send_file(tmp.name, mimetype="audio/wav", as_attachment=False, download_name="speech.wav")
    if res.get("first_audio_header_ms"):
        resp.headers["X-Atlas-First-Audio-Ms"] = str(res["first_audio_header_ms"])
    return resp


if __name__ == "__main__":
    import socket
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "5000"))
    try:
        lan = socket.gethostbyname(socket.gethostname())
    except OSError:
        lan = "127.0.0.1"
    print("=" * 60)
    print("ATLAS LIVE WEB DEMO")
    print(f"  local:   http://127.0.0.1:{port}")
    print(f"  on LAN:  http://{lan}:{port}   (share this with teammates on the same network)")
    print("=" * 60)
    print("NOTE: browsers allow the mic only on localhost or HTTPS. On the LAN")
    print("      address teammates may need to allow mic for an http:// origin,")
    print("      or you deploy behind HTTPS. See the README section I added.")
    app.run(host=host, port=port, debug=False)
