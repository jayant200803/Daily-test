"""Atlas live web demo — open in a browser, click to speak (STT) or type (TTS).

Your team opens one URL. No Python on their side. The Atlas test key stays
HERE on the server (loaded from .env), never shipped to the browser.

Endpoints hit Atlas exactly per the docs, via lib/atlas.py:
  POST /api/stt  -> atlas-listen  (/v1/audio/transcriptions)
  POST /api/tts  -> atlas-speak   (/v1/audio/speech)

Run:   python webapp.py
Then open http://127.0.0.1:5000  (or the LAN address it prints) in a browser.
"""
import os, tempfile, time, logging, base64
from flask import Flask, request, jsonify, send_file, Response, render_template_string, g

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import SPEAK_VOICE, ASSISTANT_SYSTEM, LLM_KEY_ENV, LLM_MODEL
from lib import atlas, llm

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
  <p class="sub" style="margin-top:-12px"><a href="/pipeline" style="color:#2f81f7">→ Open the Speech-to-Speech pipeline page (live transcription + latency + spoken reply)</a></p>

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

  <div class="card">
    <h2>🗣️ Talk to Atlas (speech-to-speech)</h2>
    <p class="meta" style="margin:0 0 12px">Day-2 pipeline (OpenAI-compatible): you speak &rarr; <b>atlas-listen</b> (STT) &rarr; <b>LLM</b> reply &rarr; <b>atlas-speak</b> (TTS) &rarr; you hear the answer.</p>
    <div class="row">
      <button id="talkBtn">Start talking</button>
      <span id="talkStatus" class="meta"></span>
    </div>
    <label>Language (your speech)</label>
    <select id="talkLang"><option value="en" selected>en (English)</option><option value="ja">ja (Japanese)</option></select>
    <label>Reply voice</label>
    <select id="talkVoice">
      <option value="ayaka">ayaka</option><option value="emi">emi</option><option value="kaito">kaito</option>
      <option value="sakura">sakura</option><option value="souta" selected>souta — default</option><option value="nanami">nanami</option>
      <option value="haruto">haruto</option><option value="mei">mei</option><option value="yuto">yuto</option>
      <option value="rina">rina</option><option value="ren">ren</option><option value="hajun">hajun</option>
    </select>
    <audio id="talkPlayer" controls style="width:100%; margin-top:12px; display:none"></audio>
    <div class="result" id="talkOut" style="min-height:40px; margin-top:12px"><span class="meta">Your conversation will appear here.</span></div>
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

const liveBtn = document.getElementById('liveBtn');
const liveStatus = document.getElementById('liveStatus');
const liveOut = document.getElementById('liveOut');

liveBtn.onclick = () => { liveOn ? stopLive() : startLive(); };

function escapeHtml(s){ return s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function renderLive() {
  // each finalized utterance (a phrase between pauses) on its own line, with its 3 latencies
  const lines = committedLines.map(o => {
    const tags = [];
    if (o.ttfp != null) tags.push('ttfp '+o.ttfp+'ms');
    if (o.eot  != null) tags.push('eot '+o.eot+'ms');
    const tag = tags.length ? ' <span style="color:var(--muted);font-size:12px">· '+tags.join(' · ')+'</span>' : '';
    return '<div><span style="color:var(--fg)">'+escapeHtml(o.text)+'</span>'+tag+'</div>';
  }).join('');
  const curTag = (uTTFP != null && currentLine) ? ' <span style="color:var(--muted);font-size:12px">· ttfp '+uTTFP+'ms</span>' : '';
  const cur = currentLine ? '<div><span style="color:var(--muted)">'+escapeHtml(currentLine)+'</span>'+curTag+'</div>' : '';
  liveOut.innerHTML = (lines || cur) ? (lines + cur) : '<span class="meta">Listening…</span>';
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
        // EOT (accurate) — from when speech ended in the audio (audio_end_ms) to the final
        // transcript arriving. Anchored to the audio timeline, not to event arrival gaps.
        const eot = (uAudioEnd != null) ? Math.max(0, Math.round(now - (audioEpoch + uAudioEnd))) : null;
        // TTFP already computed as uTTFP
        if (m.transcript) committedLines.push({ text: m.transcript, ttfp: uTTFP, eot: eot });
        currentLine = ''; interimActive = false;
        uFirstPartial = null; uTTFP = null; uStoppedRecv = null; uAudioEnd = null;
        renderLive(); break;
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

// ---------- Day-2: Talk to Atlas (speech -> STT -> LLM -> TTS -> speech) ----------
let talkRec = false, talkCtx, talkStream, talkProc, talkSrc, talkBufs = [], talkSr = 16000;
const talkBtn = document.getElementById('talkBtn');
const talkStatus = document.getElementById('talkStatus');
const talkOut = document.getElementById('talkOut');
const talkPlayer = document.getElementById('talkPlayer');

talkBtn.onclick = async () => { talkRec ? await talkStopSend() : await talkStart(); };

async function talkStart() {
  try { talkStream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (e) { talkOut.innerHTML = '<span class="err">Microphone blocked: '+e.message+'</span>'; return; }
  talkCtx = new (window.AudioContext || window.webkitAudioContext)();
  talkSr = talkCtx.sampleRate;
  talkSrc = talkCtx.createMediaStreamSource(talkStream);
  talkProc = talkCtx.createScriptProcessor(4096, 1, 1);
  talkBufs = [];
  talkProc.onaudioprocess = e => talkBufs.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  talkSrc.connect(talkProc); talkProc.connect(talkCtx.destination);
  talkRec = true;
  talkBtn.textContent = 'Stop & send'; talkBtn.classList.add('rec');
  talkStatus.innerHTML = '<span class="dot"></span>listening…';
}

async function talkStopSend() {
  talkRec = false;
  try { talkProc.disconnect(); talkSrc.disconnect(); talkStream.getTracks().forEach(t=>t.stop()); await talkCtx.close(); } catch {}
  talkBtn.textContent = 'Start talking'; talkBtn.classList.remove('rec');
  talkStatus.textContent = 'thinking…';

  const wav = encodeWav(flatten(talkBufs), talkSr);
  const fd = new FormData();
  fd.append('file', new Blob([wav], {type:'audio/wav'}), 'talk.wav');
  fd.append('language', document.getElementById('talkLang').value);
  fd.append('voice', document.getElementById('talkVoice').value);

  try {
    const r = await fetch('/api/talk', { method:'POST', body: fd });
    const j = await r.json();
    talkStatus.textContent = '';
    if (!j.ok) {
      const where = j.stage ? (' ['+j.stage+']') : '';
      talkOut.innerHTML = '<span class="err">Error'+where+': '+escapeHtml(j.error||('HTTP '+r.status))+'</span>'
        + (j.heard ? '<div class="meta" style="margin-top:6px">heard: "'+escapeHtml(j.heard)+'"</div>' : '');
      return;
    }
    // play the spoken reply
    const bytes = atob(j.audio_b64); const arr = new Uint8Array(bytes.length);
    for (let i=0;i<bytes.length;i++) arr[i]=bytes.charCodeAt(i);
    talkPlayer.src = URL.createObjectURL(new Blob([arr], {type:'audio/wav'}));
    talkPlayer.style.display='block'; talkPlayer.play();
    const t = j.timings || {};
    talkOut.innerHTML =
      '<div style="margin-bottom:6px"><span class="meta">You said:</span> '+escapeHtml(j.heard)+'</div>'
      + '<div><span class="meta">Atlas replied:</span> <b>'+escapeHtml(j.reply)+'</b></div>'
      + '<div class="meta" style="margin-top:8px; font-size:12px">STT '+(t.stt_ms||'?')+'ms · LLM '+(t.llm_ms||'?')+'ms · TTS first-audio '+(t.tts_first_audio_ms||'?')+'ms · '+escapeHtml(j.model||'')+'</div>';
  } catch (e) { talkStatus.textContent=''; talkOut.innerHTML = '<span class="err">'+escapeHtml(e.message)+'</span>'; }
}
</script>
</body>
</html>"""


PIPELINE_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atlas Speech-to-Speech</title>
<style>
  :root { --bg:#0f1419; --card:#1a222c; --fg:#e6edf3; --muted:#8b98a5;
          --accent:#2f81f7; --ok:#2ea043; --rec:#e5534b; --border:#30363d; }
  * { box-sizing:border-box; }
  body { margin:0; font:16px/1.5 system-ui,Segoe UI,sans-serif; background:var(--bg); color:var(--fg); }
  .wrap { max-width:760px; margin:0 auto; padding:24px 16px 64px; }
  a { color:var(--accent); text-decoration:none; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 20px; font-size:14px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; margin-bottom:20px; }
  .card h2 { font-size:16px; margin:0 0 12px; }
  label { display:block; font-size:13px; color:var(--muted); margin:12px 0 4px; }
  select { width:100%; background:#0d1117; color:var(--fg); border:1px solid var(--border); border-radius:8px; padding:9px 11px; font:inherit; }
  button { cursor:pointer; border:0; border-radius:8px; padding:11px 20px; font:inherit; font-weight:600; color:#fff; background:var(--accent); }
  button.rec { background:var(--rec); }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .result { margin-top:14px; padding:12px 14px; border-radius:8px; background:#0d1117; border:1px solid var(--border); min-height:20px; white-space:pre-wrap; }
  .err { color:var(--rec); }
  .meta { color:var(--muted); font-size:13px; }
  .dot { width:10px; height:10px; border-radius:50%; background:var(--rec); display:inline-block; margin-right:6px; animation:p 1s infinite; }
  @keyframes p { 50% { opacity:.3; } }
  .lat { display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }
  .chip { background:#0d1117; border:1px solid var(--border); border-radius:8px; padding:8px 12px; font-size:13px; }
  .chip b { font-size:18px; color:var(--fg); display:block; }
  .chip span { color:var(--muted); font-size:11px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Atlas Speech-to-Speech Pipeline</h1>
  <p class="sub">Live transcription with latency, then a spoken reply. <a href="/">&larr; back to test console</a></p>

  <div class="card">
    <h2>🗣️ Converse</h2>
    <p class="meta" style="margin:0 0 12px">Click start and just talk. When you pause, Atlas replies automatically, then listens again — like a real voice agent. You speak &rarr; <b>atlas-listen</b> (live STT) &rarr; <b>LLM</b> &rarr; <b>atlas-speak</b> (TTS) &rarr; you hear the answer.</p>
    <div class="row">
      <button id="pBtn">Start conversation</button>
      <span id="pStatus" class="meta"></span>
    </div>
    <label>Language (your speech)</label>
    <select id="pLang"><option value="en" selected>en (English)</option><option value="ja">ja (Japanese)</option></select>
    <label>Reply voice</label>
    <select id="pVoice">
      <option value="ayaka">ayaka</option><option value="emi">emi</option><option value="kaito">kaito</option>
      <option value="sakura">sakura</option><option value="souta" selected>souta — default</option><option value="nanami">nanami</option>
      <option value="haruto">haruto</option><option value="mei">mei</option><option value="yuto">yuto</option>
      <option value="rina">rina</option><option value="ren">ren</option><option value="hajun">hajun</option>
    </select>
  </div>

  <div class="card">
    <h2>📝 Live transcription (your speech)</h2>
    <div class="result" id="pOut" style="min-height:48px"><span class="meta">Your words stream here, each phrase with ttfp / eot latency.</span></div>
  </div>

  <div class="card">
    <h2>🔊 Atlas reply</h2>
    <audio id="pPlayer" controls style="width:100%; display:none; margin-bottom:10px"></audio>
    <div class="result" id="pReply"><span class="meta">Each reply shows its latency inline above (reply = LLM + TTS).</span></div>
  </div>
</div>

<script>
let ws=null, ctx=null, stream=null, proc=null, src=null, on=false, replying=false;
let audioEpoch=0, curLine='', interimActive=false;
let uAudioStart=0, uFirst=null, uTTFP=null, uAudioEnd=null;
let convo=[], turnBuf='', endTurnTimer=null, tTurnEnd=0, lastSpeechEndWall=0;
const ENDTURN_MS = 600;   // extra silence after a phrase before we treat the turn as finished (snappy but tolerates short pauses)
const pBtn=document.getElementById('pBtn'), pStatus=document.getElementById('pStatus');
const pOut=document.getElementById('pOut'), pReply=document.getElementById('pReply');
const pPlayer=document.getElementById('pPlayer');

const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
pBtn.onclick = () => { on ? hardStop() : start(); };

function latTag(l){
  if(!l) return '';
  return ' <span style="color:var(--muted);font-size:12px">· reply '+l.total+'ms (LLM '+l.llm+'ms · TTS '+l.tts+'ms)</span>';
}
function render() {
  const log = convo.map(o => o.role==='you'
    ? '<div style="margin:6px 0"><span class="meta">🧑 you:</span> '+esc(o.text)+'</div>'
    : '<div style="margin:6px 0"><span class="meta">🤖 atlas:</span> <b>'+esc(o.text)+'</b>'+latTag(o.lat)+'</div>'
  ).join('');
  const cur = curLine ? '<div style="margin:6px 0"><span class="meta">🧑 you:</span> <span style="color:var(--muted)">'+esc(curLine)+'</span></div>' : '';
  pOut.innerHTML = (log||cur) ? (log+cur) : '<span class="meta">Listening…</span>';
  pOut.scrollTop = pOut.scrollHeight;
}
function markFirst(txt){ if(uFirst===null && txt && txt.trim()){ uFirst=performance.now(); uTTFP=Math.max(0,Math.round(uFirst-(audioEpoch+uAudioStart))); } }
function scheduleEndTurn(){ clearTimeout(endTurnTimer); endTurnTimer=setTimeout(finalizeTurn, ENDTURN_MS); }

async function start() {
  convo=[]; curLine=''; interimActive=false; turnBuf=''; replying=false;
  audioEpoch=0; uAudioStart=0; uFirst=null; uTTFP=null; uAudioEnd=null;
  pReply.innerHTML='<span class="meta">…</span>'; pPlayer.style.display='none';
  pOut.innerHTML='<span class="meta">connecting…</span>';
  let key;
  try { const kr=await fetch('/api/rtkey'); const kj=await kr.json(); if(!kj.ok) throw new Error(kj.error||'no key'); key=kj.key; }
  catch(e){ pOut.innerHTML='<span class="err">Cannot get key: '+e.message+'</span>'; return; }
  try { stream=await navigator.mediaDevices.getUserMedia({audio:true}); ctx=new (window.AudioContext||window.webkitAudioContext)({sampleRate:16000}); }
  catch(e){ pOut.innerHTML='<span class="err">Microphone blocked: '+e.message+'</span>'; return; }
  const rate=ctx.sampleRate;
  ws=new WebSocket('wss://atlas.oneinbox.ai/v1/realtime?intent=transcription', ['realtime','openai-insecure-api-key.'+key]);
  ws.onopen=()=>{
    ws.send(JSON.stringify({type:'session.update',session:{type:'transcription',audio:{input:{
      format:{type:'audio/pcm',rate:rate},
      transcription:{model:'atlas-listen',language:document.getElementById('pLang').value,prompt:undefined},
      turn_detection:{type:'server_vad',silence_duration_ms:500}}},atlas:{interim:true}}}));
    src=ctx.createMediaStreamSource(stream); proc=ctx.createScriptProcessor(4096,1,1);
    proc.onaudioprocess=e=>{ if(!on||replying||ws.readyState!==1) return;   // mute mic while the agent speaks (no echo)
      const f=e.inputBuffer.getChannelData(0); const i=new Int16Array(f.length);
      for(let k=0;k<f.length;k++){let s=Math.max(-1,Math.min(1,f[k]));i[k]=s<0?s*0x8000:s*0x7FFF;} ws.send(JSON.stringify({type:'input_audio_buffer.append',audio:b64(i.buffer)})); };
    src.connect(proc); proc.connect(ctx.destination); audioEpoch=performance.now(); on=true;
    pBtn.textContent='End conversation'; pBtn.classList.add('rec'); pStatus.innerHTML='<span class="dot"></span>listening…'; render();
  };
  ws.onmessage=ev=>{ if(replying) return; let m; try{m=JSON.parse(ev.data);}catch{return;}
    switch(m.type){
      case 'input_audio_buffer.speech_started': clearTimeout(endTurnTimer); uAudioStart=(m.audio_start_ms!=null)?m.audio_start_ms:Math.round(performance.now()-audioEpoch); uFirst=null; uTTFP=null; uAudioEnd=null; curLine=''; interimActive=false; render(); break;
      case 'conversation.item.input_audio_transcription.interim': clearTimeout(endTurnTimer); interimActive=true; markFirst(m.text); curLine=m.text||''; render(); break;
      case 'conversation.item.input_audio_transcription.delta': clearTimeout(endTurnTimer); markFirst(m.delta); if(!interimActive){curLine+=(m.delta||'');render();} break;
      case 'input_audio_buffer.speech_stopped': uAudioEnd=(m.audio_end_ms!=null)?m.audio_end_ms:Math.round(performance.now()-audioEpoch); lastSpeechEndWall=audioEpoch+uAudioEnd; break;
      case 'conversation.item.input_audio_transcription.completed':
        if(m.transcript) turnBuf += (turnBuf?' ':'') + m.transcript;
        curLine=''; interimActive=false; render(); scheduleEndTurn(); break;   // wait ENDTURN_MS of silence -> reply
      case 'error': pStatus.textContent=''; pOut.innerHTML='<span class="err">Atlas error: '+((m.error&&(m.error.message||m.error.code))||'unknown')+'</span>'; hardStop(); break;
    }
  };
  ws.onerror=()=>{ if(on) pOut.innerHTML='<span class="err">WebSocket error (check key / network)</span>'; };
}

function hardStop() {
  on=false; replying=false; clearTimeout(endTurnTimer);
  try{if(proc)proc.disconnect();}catch{} try{if(src)src.disconnect();}catch{}
  try{if(stream)stream.getTracks().forEach(t=>t.stop());}catch{} try{if(ctx)ctx.close();}catch{}
  try{if(ws&&ws.readyState===1)ws.close();}catch{}
  pBtn.textContent='Start conversation'; pBtn.classList.remove('rec'); pStatus.textContent='';
}

async function finalizeTurn() {
  const text = turnBuf.trim(); turnBuf='';
  if(!text || replying || !on) return;
  replying=true;                         // stop listening while we reply (prevents echo)
  const tStart=performance.now();        // reliable wall-clock: turn captured -> reply ready
  convo.push({role:'you', text}); render();
  pStatus.innerHTML='<span class="dot"></span>thinking…';
  try {
    // full conversation (incl. this turn) so the model remembers context
    const history = convo.map(o => ({ role: o.role==='atlas' ? 'assistant' : 'user', content: o.text }));
    // 1) LLM only (fast)
    const r=await fetch('/api/respond',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text, history})});
    const j=await r.json();
    if(!j.ok){ pReply.innerHTML='<span class="err">Error'+(j.stage?(' ['+j.stage+']'):'')+': '+esc(j.error||('HTTP '+r.status))+'</span>'; resumeListening(); return; }
    const llmDone=performance.now();

    // 2) fetch the (short) mp3 fully, THEN play — avoids mid-reply underrun/stall.
    const voice=document.getElementById('pVoice').value;
    const url='/api/speak_stream?format=mp3&voice='+encodeURIComponent(voice)+'&text='+encodeURIComponent(j.reply);
    const ar=await fetch(url);
    if(!ar.ok){ const ej=await ar.json().catch(()=>({})); pReply.innerHTML='<span class="err">TTS error: '+esc(ej.error||('HTTP '+ar.status))+'</span>'; resumeListening(); return; }
    const blob=await ar.blob();
    pPlayer.src=URL.createObjectURL(blob); pPlayer.style.display='block';

    // per-reply latency (reliable wall-clock): total = turn captured -> reply ready
    const total=Math.round(performance.now()-tStart);
    const tts=Math.round(performance.now()-llmDone);
    const llm=(j.timings&&j.timings.llm_ms!=null)?j.timings.llm_ms:Math.max(0,total-tts);
    convo.push({role:'atlas', text:j.reply, lat:{total, llm, tts}}); render();
    pReply.innerHTML='<span class="meta">Latency is shown next to each reply above.</span>';

    pPlayer.onended = () => resumeListening();
    pPlayer.onerror = () => resumeListening();
    pPlayer.play().catch(()=>{ resumeListening(); });
  } catch(e){ pReply.innerHTML='<span class="err">'+esc(e.message)+'</span>'; resumeListening(); }
}

function resumeListening(){
  replying=false;
  if(on) pStatus.innerHTML='<span class="dot"></span>listening…';
}

function chip(v,label,sub){ return '<div class="chip"><b>'+(v!=null?v+'ms':'—')+'</b>'+esc(label)+'<br><span>'+sub+'</span></div>'; }
function b64(buf){ const b=new Uint8Array(buf); let s=''; for(let i=0;i<b.length;i++) s+=String.fromCharCode(b[i]); return btoa(s); }
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


@app.get("/pipeline")
def pipeline():
    return render_template_string(PIPELINE_PAGE)


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


@app.post("/api/talk")
def api_talk():
    """Day-2 full speech-to-speech pipeline (OpenAI-compatible integration):
    mic audio -> atlas-listen (STT) -> LLM reply -> atlas-speak (TTS) -> audio back.
    Returns JSON with the transcript, the reply text, the reply audio (base64), and
    per-stage timings."""
    missing = _key_missing()
    if missing:
        return missing
    if not os.environ.get(LLM_KEY_ENV):
        return jsonify(ok=False, stage="llm",
                       error=f"{LLM_KEY_ENV} is not set on the server. Add it in Vercel "
                             f"env vars and redeploy."), 500
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="no audio file"), 400
    language = request.form.get("language", "en") or "en"
    voice = (request.form.get("voice") or SPEAK_VOICE).strip()

    tmp_in = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_out.close()
    try:
        f.save(tmp_in.name)
        tmp_in.close()

        # 1) STT
        stt = atlas.transcribe(tmp_in.name, language=language, content_type="audio/wav")
        heard = (stt.get("text") or "").strip()
        if stt.get("status") != 200 or not heard:
            g._detail = f"TALK stt_fail status={stt.get('status')}"
            return jsonify(ok=False, stage="stt", status=stt.get("status"),
                           error="Could not transcribe audio", detail=stt.get("raw"),
                           heard=heard), 502

        # 2) LLM reply
        chat = llm.chat(heard, system=ASSISTANT_SYSTEM)
        reply = (chat.get("text") or "").strip()
        if chat.get("status") != 200 or not reply:
            g._detail = f"TALK llm_fail status={chat.get('status')}"
            return jsonify(ok=False, stage="llm", status=chat.get("status"),
                           error=chat.get("error") or "LLM returned no text",
                           heard=heard), 502

        # 3) TTS
        tts = atlas.synthesize(_clean_tts_text(reply), voice=voice, out_path=tmp_out.name, response_format="wav")
        if tts.get("status") != 200 or not tts.get("bytes"):
            g._detail = f"TALK tts_fail status={tts.get('status')}"
            return jsonify(ok=False, stage="tts", status=tts.get("status"),
                           error=f"atlas-speak returned {tts.get('status')}",
                           heard=heard, reply=reply), 502

        with open(tmp_out.name, "rb") as fh:
            audio_b64 = base64.b64encode(fh.read()).decode("ascii")

        g._detail = (f"TALK ok heard={len(heard)}c reply={len(reply)}c "
                     f"stt={stt.get('elapsed_ms')}ms llm={chat.get('elapsed_ms')}ms "
                     f"tts_first={tts.get('first_audio_header_ms')}ms voice={voice}")
        return jsonify(ok=True, heard=heard, reply=reply, audio_b64=audio_b64,
                       model=LLM_MODEL, voice=voice,
                       timings={"stt_ms": stt.get("elapsed_ms"),
                                "llm_ms": chat.get("elapsed_ms"),
                                "tts_first_audio_ms": tts.get("first_audio_header_ms"),
                                "tts_total_ms": tts.get("total_ms")})
    finally:
        for p in (tmp_in.name, tmp_out.name):
            try: os.unlink(p)
            except OSError: pass


@app.post("/api/respond")
def api_respond():
    """LLM-ONLY from already-transcribed text (used by the /pipeline page). TTS is
    fetched separately via /api/speak_stream so audio playback can start at first
    audio instead of waiting for the whole clip."""
    if not os.environ.get(LLM_KEY_ENV):
        return jsonify(ok=False, stage="llm", error=f"{LLM_KEY_ENV} is not set on the server."), 500
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    history = data.get("history")
    if not text and not history:
        return jsonify(ok=False, error="no text"), 400
    # history (full conversation incl. latest user turn) gives the model memory.
    if isinstance(history, list) and history:
        chat = llm.chat(system=ASSISTANT_SYSTEM, history=history[-16:])
    else:
        chat = llm.chat(text, system=ASSISTANT_SYSTEM)
    reply = (chat.get("text") or "").strip()
    if chat.get("status") != 200 or not reply:
        g._detail = f"RESPOND llm_fail status={chat.get('status')}"
        return jsonify(ok=False, stage="llm", status=chat.get("status"),
                       error=chat.get("error") or "LLM returned no text"), 502
    g._detail = f"RESPOND ok reply={len(reply)}c llm={chat.get('elapsed_ms')}ms"
    return jsonify(ok=True, reply=reply, model=LLM_MODEL,
                   timings={"llm_ms": chat.get("elapsed_ms")})


def _clean_tts_text(s):
    """Normalize LLM punctuation that can trip up TTS (smart quotes, dashes, ellipsis)."""
    repl = {"’": "'", "‘": "'", "“": '"', "”": '"',
            "‑": "-", "–": "-", "—": "-", "…": "...", " ": " "}
    for a, b in repl.items():
        s = s.replace(a, b)
    return s


@app.get("/api/speak_stream")
def api_speak_stream():
    """Stream atlas-speak audio straight to the browser so playback starts at first
    audio (low latency). mp3 by default. Retries transient Atlas errors (429/5xx)."""
    missing = _key_missing()
    if missing:
        return missing
    text = _clean_tts_text((request.args.get("text") or "").strip())
    if not text:
        return jsonify(ok=False, error="no text"), 400
    voice = (request.args.get("voice") or SPEAK_VOICE).strip()
    fmt = (request.args.get("format") or "mp3").strip()
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg", "pcm": "audio/basic"}.get(fmt, "audio/mpeg")

    upstream = None
    last = None
    for attempt in range(3):
        upstream = atlas.speak_stream(text, voice=voice, response_format=fmt)
        if upstream.status_code == 200:
            break
        last = upstream.status_code
        upstream.close()
        if upstream.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(0.5 * (attempt + 1))   # brief backoff, then retry
            continue
        break
    if upstream is None or upstream.status_code != 200:
        g._detail = f"SPEAK_STREAM fail status={last}"
        return jsonify(ok=False, error=f"atlas-speak returned {last}",
                       detail="Atlas was temporarily unavailable; please try again."), 502
    g._detail = f"SPEAK_STREAM ok voice={voice} fmt={fmt} chars={len(text)}"

    def generate():
        for chunk in upstream.iter_content(chunk_size=2048):
            if chunk:
                yield chunk
    resp = Response(generate(), mimetype=mime)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.get("/api/talk/health")
def api_talk_health():
    """Report whether both keys the pipeline needs are present + reachable."""
    out = {"atlas_key": bool(os.environ.get("ATLAS_API_KEY")),
           "llm_key": bool(os.environ.get(LLM_KEY_ENV)), "llm_model": LLM_MODEL}
    try:
        code, _ = atlas.list_models()
        out["atlas_status"] = code
    except Exception as e:
        out["atlas_status"] = f"err:{e}"
    if out["llm_key"]:
        probe = llm.chat("ping", system="Reply with the word pong only.")
        out["llm_status"] = probe.get("status")
        if probe.get("error"):
            out["llm_error"] = probe["error"]
    out["ok"] = out["atlas_key"] and out["llm_key"] and out.get("atlas_status") == 200 and out.get("llm_status") == 200
    return jsonify(out), (200 if out["ok"] else 500)


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
