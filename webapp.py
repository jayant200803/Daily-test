"""Atlas live web demo — open in a browser, click to speak (STT) or type (TTS).

Your team opens one URL. No Python on their side. The Atlas test key stays
HERE on the server (loaded from .env), never shipped to the browser.

Endpoints hit Atlas exactly per the docs, via lib/atlas.py:
  POST /api/stt  -> atlas-listen  (/v1/audio/transcriptions)
  POST /api/tts  -> atlas-speak   (/v1/audio/speech)

Run:   python webapp.py
Then open http://127.0.0.1:5000  (or the LAN address it prints) in a browser.
"""
import os, tempfile, time, logging
from flask import Flask, request, jsonify, send_file, Response, render_template_string, g

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import SPEAK_VOICE
from lib import atlas

app = Flask(__name__)

# ---- Per-request logging (prints one line per request; shows in Vercel > Logs) ----
# Additive only: wrapped in try/except so logging can never break a request.
logging.basicConfig(level=logging.INFO, format="%(message)s")
_log = logging.getLogger("atlas-web")


def _client_ip():
    # Behind Vercel the real client IP is in X-Forwarded-For (first entry).
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "-")


@app.before_request
def _start_timer():
    g._t0 = time.time()
    g._detail = ""          # handlers may fill this with STT/TTS specifics


@app.after_request
def _log_request(resp):
    try:
        ms = int((time.time() - getattr(g, "_t0", time.time())) * 1000)
        detail = getattr(g, "_detail", "") or ""
        _log.info("REQ %s %s -> %s %dms ip=%s %s",
                  request.method, request.path, resp.status_code, ms, _client_ip(), detail)
    except Exception:
        pass        # never let logging interfere with the response
    return resp

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
    <h2>🔴 Live transcription (streaming) &rarr; atlas-listen</h2>
    <p class="meta" style="margin:0 0 12px">Words appear <b>while you speak</b> over a WebSocket. Interim words show dim; finalized text turns solid.</p>
    <div class="row">
      <button id="liveBtn">Start live</button>
      <span id="liveStatus" class="meta"></span>
    </div>
    <label>Language</label>
    <select id="liveLang"><option value="en" selected>en (English)</option><option value="ja">ja (Japanese)</option></select>
    <label>Vocabulary hint (optional, comma-separated words)</label>
    <input type="text" id="livePrompt" placeholder="e.g. OneInbox, Exotel, NS1156">
    <div class="result" id="liveOut" style="min-height:60px"><span class="meta">Live transcript will stream here.</span></div>
    <div class="meta" id="liveSummary" style="margin-top:10px"></div>
    <p class="meta" style="margin:8px 0 0; font-size:11px">Latency (market-standard, measured client-side over the real network):
      <b>TTFP</b> time to first partial · <b>Lag</b> transcript latency vs audio · <b>EOT</b> end-of-speech &rarr; final. Summary shows p50/p95.</p>
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

// ---------- LIVE streaming STT over WebSocket (browser -> Atlas directly) ----------
// NOTE: the Atlas key is fetched from /api/rtkey and used in the WS subprotocol.
// Per the Atlas docs this browser form exposes the key to anyone who opens the page;
// acceptable only for a TEST key. Everything here is additive to the batch STT above.
let liveWS = null, liveCtx = null, liveStream = null, liveProc = null, liveSrc = null, liveOn = false;
let committedLines = [], currentLine = "", interimActive = false;

// ---- Latency metrics (market-standard, client-side over the real network) ----
// audioEpoch: wall-clock (performance.now) at audio position 0, so audio ms X => audioEpoch + X.
let audioEpoch = 0;
// per-utterance timing
let uAudioStart = 0, uFirstPartial = null, uStoppedRecv = null, uAudioEnd = null, uTTFP = null;
// session samples for percentiles
let mTTFP = [], mLAG = [], mEOT = [];

const liveBtn = document.getElementById('liveBtn');
const liveStatus = document.getElementById('liveStatus');
const liveOut = document.getElementById('liveOut');
const liveSummary = document.getElementById('liveSummary');

liveBtn.onclick = () => { liveOn ? stopLive() : startLive(); };

function escapeHtml(s){ return s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function renderLive() {
  // each finalized utterance (a phrase between pauses) on its own line, with its 3 latencies
  const lines = committedLines.map(o => {
    const tags = [];
    if (o.ttfp != null) tags.push('ttfp '+o.ttfp+'ms');
    if (o.lag  != null) tags.push('lag '+o.lag+'ms');
    if (o.eot  != null) tags.push('eot '+o.eot+'ms');
    const tag = tags.length ? ' <span style="color:var(--muted);font-size:12px">· '+tags.join(' · ')+'</span>' : '';
    return '<div><span style="color:var(--fg)">'+escapeHtml(o.text)+'</span>'+tag+'</div>';
  }).join('');
  const curTag = (uTTFP != null && currentLine) ? ' <span style="color:var(--muted);font-size:12px">· ttfp '+uTTFP+'ms</span>' : '';
  const cur = currentLine ? '<div><span style="color:var(--muted)">'+escapeHtml(currentLine)+'</span>'+curTag+'</div>' : '';
  liveOut.innerHTML = (lines || cur) ? (lines + cur) : '<span class="meta">Listening…</span>';
}

function pct(arr, p) {
  if (!arr.length) return null;
  const s = [...arr].sort((a,b)=>a-b);
  const i = Math.min(s.length-1, Math.max(0, Math.ceil(p/100*s.length)-1));
  return s[i];
}
function fmtMetric(name, arr) {
  if (!arr.length) return name+': —';
  return name+': p50 '+pct(arr,50)+'ms · p95 '+pct(arr,95)+'ms (n='+arr.length+')';
}
function renderSummary() {
  if (!mTTFP.length && !mLAG.length && !mEOT.length) { liveSummary.innerHTML = ''; return; }
  liveSummary.innerHTML = '<b>Session latency</b> &nbsp; '
    + fmtMetric('TTFP', mTTFP) + ' &nbsp;|&nbsp; '
    + fmtMetric('Lag', mLAG)   + ' &nbsp;|&nbsp; '
    + fmtMetric('EOT', mEOT);
}

function markFirstPartial(txt){
  if (uFirstPartial === null && txt && txt.trim()) {
    uFirstPartial = performance.now();
    // TTFP vs when speech began in the audio timeline
    uTTFP = Math.max(0, Math.round(uFirstPartial - (audioEpoch + uAudioStart)));
    liveStatus.innerHTML = '<span class="dot"></span>live · ttfp '+uTTFP+'ms';
  }
}

async function startLive() {
  committedLines = []; currentLine = ""; interimActive = false;
  audioEpoch = 0; uAudioStart = 0; uFirstPartial = null; uTTFP = null; uStoppedRecv = null; uAudioEnd = null;
  mTTFP = []; mLAG = []; mEOT = []; liveSummary.innerHTML = '';
  liveOut.innerHTML = '<span class="meta">connecting…</span>';
  // 1) get the key from our server (test key; see note above)
  let key;
  try {
    const kr = await fetch('/api/rtkey'); const kj = await kr.json();
    if (!kj.ok) throw new Error(kj.error || 'no key'); key = kj.key;
  } catch (e) { liveOut.innerHTML = '<span class="err">Cannot get key: '+e.message+'</span>'; return; }

  // 2) mic at 16 kHz (a rate Atlas accepts: 8/16/24/48 kHz)
  try {
    liveStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    liveCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  } catch (e) { liveOut.innerHTML = '<span class="err">Microphone blocked: '+e.message+'</span>'; return; }
  const rate = liveCtx.sampleRate;   // may be 16000 (or the nearest the browser allows)

  // 3) open the WebSocket straight to Atlas, authed via subprotocol
  liveWS = new WebSocket('wss://atlas.oneinbox.ai/v1/realtime?intent=transcription',
                         ['realtime', 'openai-insecure-api-key.' + key]);

  liveWS.onopen = () => {
    liveWS.send(JSON.stringify({
      type: 'session.update',
      session: {
        type: 'transcription',
        audio: { input: {
          format: { type: 'audio/pcm', rate: rate },
          transcription: {
            model: 'atlas-listen',
            language: document.getElementById('liveLang').value,
            prompt: document.getElementById('livePrompt').value || undefined
          },
          turn_detection: { type: 'server_vad', silence_duration_ms: 500 }
        }},
        atlas: { interim: true }
      }
    }));
    // start streaming mic once the socket is open
    liveSrc = liveCtx.createMediaStreamSource(liveStream);
    liveProc = liveCtx.createScriptProcessor(4096, 1, 1);
    liveProc.onaudioprocess = e => {
      if (!liveOn || liveWS.readyState !== 1) return;
      const f32 = e.inputBuffer.getChannelData(0);
      const i16 = new Int16Array(f32.length);
      for (let i = 0; i < f32.length; i++) { let s = Math.max(-1, Math.min(1, f32[i])); i16[i] = s < 0 ? s*0x8000 : s*0x7FFF; }
      liveWS.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: b64(i16.buffer) }));
    };
    liveSrc.connect(liveProc); liveProc.connect(liveCtx.destination);
    audioEpoch = performance.now();   // audio position 0 starts now (anchor for latency)
    liveOn = true;
    liveBtn.textContent = 'Stop live'; liveBtn.classList.add('rec');
    liveStatus.innerHTML = '<span class="dot"></span>live';
    renderLive();
  };

  liveWS.onmessage = ev => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    switch (m.type) {
      case 'input_audio_buffer.speech_started':
        uAudioStart = (m.audio_start_ms != null) ? m.audio_start_ms : Math.round(performance.now() - audioEpoch);
        uFirstPartial = null; uTTFP = null; uStoppedRecv = null; uAudioEnd = null;
        currentLine = ''; interimActive = false; renderLive(); break;
      case 'conversation.item.input_audio_transcription.interim':
        interimActive = true; markFirstPartial(m.text); currentLine = m.text || ''; renderLive(); break;
      case 'conversation.item.input_audio_transcription.delta':
        markFirstPartial(m.delta);
        if (!interimActive) { currentLine += (m.delta || ''); renderLive(); } break;
      case 'input_audio_buffer.speech_stopped':
        uStoppedRecv = performance.now();
        uAudioEnd = (m.audio_end_ms != null) ? m.audio_end_ms : Math.round(uStoppedRecv - audioEpoch);
        break;
      case 'conversation.item.input_audio_transcription.completed': {
        const now = performance.now();
        // Metric 1 — transcript latency (lag): how far behind the audio the final landed
        const lag = (uAudioEnd != null) ? Math.max(0, Math.round(now - (audioEpoch + uAudioEnd))) : null;
        // Metric 2 — EOT latency: end-of-speech event -> final transcript
        const eot = (uStoppedRecv != null) ? Math.max(0, Math.round(now - uStoppedRecv)) : null;
        // Metric 3 — TTFP already computed as uTTFP
        if (uTTFP != null) mTTFP.push(uTTFP);
        if (lag   != null) mLAG.push(lag);
        if (eot   != null) mEOT.push(eot);
        if (m.transcript) committedLines.push({ text: m.transcript, ttfp: uTTFP, lag: lag, eot: eot });
        currentLine = ''; interimActive = false;
        uFirstPartial = null; uTTFP = null; uStoppedRecv = null; uAudioEnd = null;
        renderLive(); renderSummary(); break;
      }
      case 'error':
        liveStatus.textContent = '';
        liveOut.innerHTML = '<span class="err">Atlas error: '+((m.error&&(m.error.message||m.error.code))||'unknown')+'</span>';
        stopLive(); break;
    }
  };
  liveWS.onerror = () => { liveStatus.textContent=''; if(liveOn) liveOut.innerHTML = '<span class="err">WebSocket error (check key / network)</span>'; };
  liveWS.onclose = () => { if (liveOn) stopLive(); };
}

function stopLive() {
  liveOn = false;
  try { if (liveProc) liveProc.disconnect(); } catch {}
  try { if (liveSrc) liveSrc.disconnect(); } catch {}
  try { if (liveStream) liveStream.getTracks().forEach(t => t.stop()); } catch {}
  try { if (liveCtx) liveCtx.close(); } catch {}
  try { if (liveWS && liveWS.readyState === 1) liveWS.close(); } catch {}
  liveBtn.textContent = 'Start live'; liveBtn.classList.remove('rec');
  liveStatus.textContent = '';
}

// base64-encode an ArrayBuffer of 16-bit PCM
function b64(buf) {
  const bytes = new Uint8Array(buf); let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}
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


@app.get("/api/rtkey")
def api_rtkey():
    """Hand the Atlas key to the browser for the direct-to-Atlas streaming WebSocket.
    WARNING: this exposes the key to anyone who opens the page. Intended for a TEST
    key only (the team chose browser-direct streaming over a server-side proxy)."""
    key = os.environ.get("ATLAS_API_KEY")
    if not key:
        return jsonify(ok=False, error="ATLAS_API_KEY not set on server"), 500
    g._detail = "RTKEY issued (browser streaming)"
    return jsonify(ok=True, key=key.strip())


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
        size = os.path.getsize(tmp.name) if os.path.exists(tmp.name) else 0
        res = atlas.transcribe(tmp.name, language=language, prompt=prompt, content_type="audio/wav")
        ok = res.get("status") == 200 and "text" in res
        g._detail = ("STT lang=%s prompt=%r in=%dB atlas=%s %s"
                     % (language, prompt or "", size, res.get("status"),
                        "ok" if ok else ("err:" + str(res.get("raw"))[:80])))
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
        g._detail = ("TTS voice=%s chars=%d atlas=%s err:%s"
                     % (voice, len(text), res.get("status"), str(detail)[:80]))
        try: os.unlink(tmp.name)
        except OSError: pass
        return jsonify(ok=False, error=f"atlas-speak returned {res.get('status')}", detail=detail), 502
    g._detail = ("TTS voice=%s chars=%d out=%dB first_audio=%sms"
                 % (voice, len(text), res.get("bytes", 0), res.get("first_audio_header_ms")))
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
