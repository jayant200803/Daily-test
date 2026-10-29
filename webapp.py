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

import concurrent.futures as _cf
from config import SPEAK_VOICE, ASSISTANT_SYSTEM, LLM_KEY_ENV, LLM_MODEL
from lib import atlas, llm
from benchmark import providers as bench
from benchmark import streaming as bstream

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
  <p class="sub" style="margin-top:-12px"><a href="/benchmark" style="color:#2f81f7">→ Open the Voice Benchmark (compare Atlas vs ElevenLabs, Cartesia, Deepgram, AssemblyAI — live)</a></p>

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
    <textarea id="ttsText">お電話ありがとうございます。マニュライフ・ファイナンスのバーチャルアシスタント、ルイと申します。本日はどのようなご用件でしょうか？</textarea>
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
          turn_detection: { type: 'server_vad', silence_duration_ms: 900 }
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
    <select id="pLang"><option value="ja" selected>ja (Japanese)</option><option value="en">en (English)</option></select>
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

  <div class="card">
    <h2>⏱️ Latency log (step by step)</h2>
    <p class="meta" style="margin:0 0 10px">Every stage with its wall-clock time and ms since the turn started — like backend logs.</p>
    <pre id="pLog" style="margin:0; padding:12px; background:#0d1117; border:1px solid var(--border); border-radius:8px; font:12px/1.5 ui-monospace,Consolas,monospace; color:var(--fg); max-height:260px; overflow:auto; white-space:pre-wrap"></pre>
  </div>
</div>

<script>
let ws=null, ctx=null, stream=null, proc=null, src=null, on=false, replying=false;
let audioEpoch=0, curLine='', interimActive=false;
let uAudioStart=0, uFirst=null, uTTFP=null, uAudioEnd=null;
let convo=[], turnBuf='', endTurnTimer=null, tTurnEnd=0, lastSpeechEndWall=0, tStart=0;
const ENDTURN_MS = 1500;   // wait this long after a phrase before finalizing the turn, so slow speakers who pause mid-sentence are NOT cut off
const pBtn=document.getElementById('pBtn'), pStatus=document.getElementById('pStatus');
const pOut=document.getElementById('pOut'), pReply=document.getElementById('pReply');
const pPlayer=document.getElementById('pPlayer');
const pLog=document.getElementById('pLog');
function logLine(label){
  const now=new Date();
  const hh=now.toTimeString().slice(0,8);
  const mmm=String(now.getMilliseconds()).padStart(3,'0');
  const dt=Math.round(performance.now()-tStart);
  pLog.textContent += '['+hh+'.'+mmm+']  +'+String(dt).padStart(5,' ')+' ms  '+label+'\\n';
  pLog.scrollTop=pLog.scrollHeight;
}

const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
pBtn.onclick = () => { on ? hardStop() : start(); };

function latTag(l){
  if(!l) return '';
  const a = (l.ttft!=null?'LLM TTFT '+l.ttft+'ms':'LLM TTFT —');
  const b = (l.ttfa!=null?'TTS TTFA '+l.ttfa+'ms':'TTS TTFA —');
  return ' <span style="color:var(--muted);font-size:12px">· '+a+' · '+b+'</span>';
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
      turn_detection:{type:'server_vad',silence_duration_ms:900}}},atlas:{interim:true}}}));
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
  tStart=performance.now();              // reliable wall-clock: turn captured -> reply ready
  convo.push({role:'you', text}); render();
  logLine('── TURN ── you: "'+text+'"');
  logLine('turn captured (you stopped speaking)');
  pStatus.innerHTML='<span class="dot"></span>thinking…';
  try {
    // full conversation (incl. this turn) so the model remembers context
    const history = convo.map(o => ({ role: o.role==='atlas' ? 'assistant' : 'user', content: o.text }));
    // 1) LLM only (fast) — 20s safety timeout
    logLine('LLM request sent');
    const r=await fetch('/api/respond',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text, history}), signal:AbortSignal.timeout(20000)});
    const j=await r.json();
    if(!j.ok){ logLine('LLM ERROR: '+(j.error||('HTTP '+r.status))); pReply.innerHTML='<span class="err">Error'+(j.stage?(' ['+j.stage+']'):'')+': '+esc(j.error||('HTTP '+r.status))+'</span>'; resumeListening(); return; }
    const ttft=(j.timings&&j.timings.llm_ttft_ms!=null)?j.timings.llm_ttft_ms:null;   // LLM Time To First Token
    logLine('LLM reply received (TTFT '+(ttft!=null?ttft+' ms':'?')+'): "'+j.reply+'"');

    // show the reply text immediately; latency fills in after TTS
    const entry={role:'atlas', text:j.reply, lat:null};
    convo.push(entry); render();
    pReply.innerHTML='<span class="meta">Per reply: LLM TTFT (first token) + TTS TTFA (first audio).</span>';

    // 2) TTS — PROGRESSIVE playback (MediaSource) so you HEAR the reply at ~first audio,
    //    matching the TTFA number. Falls back to buffered blob if MSE isn't available.
    const voice=document.getElementById('pVoice').value;
    const url='/api/speak_stream?format=mp3&voice='+encodeURIComponent(voice)+'&text='+encodeURIComponent(j.reply);

    pPlayer.style.display='block';
    let fsLogged=false;
    pPlayer.onplaying = () => { if(!fsLogged){ fsLogged=true; logLine('FIRST SOUND 🔊 (felt '+Math.round(performance.now()-tStart)+' ms from turn end)'); } };
    pPlayer.onended = () => { logLine('audio finished'); resumeListening(); };
    pPlayer.onerror = () => { resumeListening(); };
    const wd=setTimeout(()=>{ if(replying){ resumeListening(); } }, 30000);
    pPlayer.addEventListener('ended', ()=>clearTimeout(wd), {once:true});
    pPlayer.addEventListener('error', ()=>clearTimeout(wd), {once:true});

    logLine('TTS request sent');
    let ttfa=null;
    try {
      ttfa = await playReply(url);           // progressive, returns Atlas TTFA
    } catch(err){
      clearTimeout(wd);
      const msg = (err&&err.ttsError) ? err.ttsError : ((err&&err.name==='TimeoutError')?'TTS timed out — try again':'TTS playback error');
      logLine('TTS ERROR: '+msg); pReply.innerHTML='<span class="err">'+esc(msg)+'</span>'; resumeListening(); return;
    }
    entry.lat={ttft, ttfa}; render();
    logLine('TTS streaming (TTFA '+(ttfa!=null?ttfa+' ms':'?')+', mode '+(window.__mseUsed?'MediaSource':'buffered')+')');
  } catch(e){
    const msg = (e && (e.name==='TimeoutError'||e.name==='AbortError')) ? 'Timed out — Atlas was slow. Please try again.' : (e && e.message) || 'error';
    pReply.innerHTML='<span class="err">'+esc(msg)+'</span>'; resumeListening();
  }
}

function resumeListening(){
  replying=false;
  if(on) pStatus.innerHTML='<span class="dot"></span>listening…';
}

function chip(v,label,sub){ return '<div class="chip"><b>'+(v!=null?v+'ms':'—')+'</b>'+esc(label)+'<br><span>'+sub+'</span></div>'; }
function b64(buf){ const b=new Uint8Array(buf); let s=''; for(let i=0;i<b.length;i++) s+=String.fromCharCode(b[i]); return btoa(s); }

// Fetch the TTS mp3 and play it. Uses MediaSource to start sound at the first chunk
// (so what you HEAR matches the TTFA number). Falls back to buffered blob if MSE
// isn't supported. Returns Atlas's TTFA (X-Atlas-First-Audio-Ms).
async function playReply(url){
  const resp = await fetch(url, {signal:AbortSignal.timeout(25000)});
  if(!resp.ok){ const ej=await resp.json().catch(()=>({})); throw {ttsError: (ej.error||('HTTP '+resp.status))}; }
  const h=resp.headers.get('X-Atlas-First-Audio-Ms');
  const ttfa = h!=null ? (parseInt(h,10)||null) : null;
  const canMSE = ('MediaSource' in window) && MediaSource.isTypeSupported('audio/mpeg') && resp.body && resp.body.getReader;
  window.__mseUsed = !!canMSE;
  if(canMSE){
    try {
      const ms=new MediaSource();
      pPlayer.src=URL.createObjectURL(ms);
      await new Promise(r=>ms.addEventListener('sourceopen', r, {once:true}));
      const sb=ms.addSourceBuffer('audio/mpeg');
      const reader=resp.body.getReader();
      pPlayer.play().catch(()=>{});
      while(true){
        const {done,value}=await reader.read();
        if(done) break;
        await new Promise((res,rej)=>{
          sb.addEventListener('updateend', res, {once:true});
          try { sb.appendBuffer(value); } catch(e){ rej(e); }
        });
      }
      if(ms.readyState==='open'){ try{ ms.endOfStream(); }catch(e){} }
      return ttfa;
    } catch(e){
      window.__mseUsed=false;   // MSE failed mid-stream; nothing else we can do without re-fetch
      throw e;
    }
  }
  // fallback: buffer fully, then play (reliable, slightly higher felt latency)
  const blob=await resp.blob();
  pPlayer.src=URL.createObjectURL(blob);
  pPlayer.play().catch(()=>{});
  return ttfa;
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


@app.get("/pipeline")
def pipeline():
    return render_template_string(PIPELINE_PAGE)


BENCH_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Voice Benchmark</title>
<style>
 :root{--bg:#0f1419;--card:#1a222c;--fg:#e6edf3;--muted:#8b98a5;--accent:#2f81f7;--ok:#2ea043;--border:#30363d;}
 body{margin:0;font:16px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--fg);}
 .wrap{max-width:900px;margin:0 auto;padding:24px 16px 64px;} a{color:var(--accent);text-decoration:none;}
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
 <h1>Voice Provider Benchmark</h1>
 <p class="sub">Run the comparison live. The SAME text/audio is sent to each provider and measured now, over the real network. <a href="/">&larr; back</a></p>

 <div class="card">
  <h2>🔊 TTS speed — Atlas vs ElevenLabs vs Cartesia</h2>
  <label>Text to speak</label>
  <textarea id="ttsText">Thank you for calling Manulife Finance, how may I help you today?</textarea>
  <label>Language</label>
  <select id="ttsLang"><option value="en" selected>English</option><option value="ja">Japanese</option></select>
  <button id="ttsBtn">Compare TTS</button>
  <div id="ttsOut"></div>
 </div>

 <div class="card">
  <h2>📝 STT accuracy — Atlas vs Deepgram vs AssemblyAI</h2>
  <p class="meta">We generate one clean clip of this text, then every provider transcribes the SAME clip.</p>
  <label>Reference text (the correct words)</label>
  <textarea id="sttText">I would like to transfer five thousand dollars to my savings account.</textarea>
  <label>Language</label>
  <select id="sttLang"><option value="en" selected>English (WER)</option><option value="ja">Japanese (CER)</option></select>
  <button id="sttBtn">Compare STT</button>
  <div id="sttOut"></div>
 </div>
 <div class="card">
  <h2>🎤 Live STT — speak and compare (your real voice)</h2>
  <p class="meta">Press record, say a sentence, stop. Your actual voice is sent to every STT provider at once — see what each heard and how fast. Optionally type what you said to also get accuracy.</p>
  <div class="row"><button id="recBtn">Start recording</button><span id="recStatus" class="meta"></span></div>
  <label>Language</label>
  <select id="liveLang"><option value="en" selected>English</option><option value="ja">Japanese</option></select>
  <label>What you said (optional — for accuracy)</label>
  <input type="text" id="liveRef" placeholder="e.g. I would like to transfer five thousand dollars">
  <div id="liveOut"></div>
 </div>
 <p class="meta">Notes: lower processing/first-byte = voice starts sooner; higher accuracy = better. AssemblyAI runs in batch mode, so compare its accuracy, not its latency.</p>
</div>
<script>
const el=id=>document.getElementById(id);
const esc=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
// ---- live mic STT comparison ----
let recing=false, actx, mstream, proc, src, bufs=[], sr=16000;
const recBtn=el('recBtn'), recStatus=el('recStatus'), liveOut=el('liveOut');
recBtn.onclick=async()=>{ recing ? await stopRec() : await startRec(); };
async function startRec(){
 try{ mstream=await navigator.mediaDevices.getUserMedia({audio:true}); }
 catch(e){ liveOut.innerHTML='<p class="err">Microphone blocked: '+e.message+'</p>'; return; }
 actx=new (window.AudioContext||window.webkitAudioContext)({sampleRate:16000}); sr=actx.sampleRate;
 src=actx.createMediaStreamSource(mstream); proc=actx.createScriptProcessor(4096,1,1); bufs=[];
 proc.onaudioprocess=e=>bufs.push(new Float32Array(e.inputBuffer.getChannelData(0)));
 src.connect(proc); proc.connect(actx.destination); recing=true;
 recBtn.textContent='Stop & compare'; recBtn.classList.add?recBtn.classList.add('rec'):0; recStatus.innerHTML='<span class="meta">● recording…</span>';
}
async function stopRec(){
 recing=false; try{proc.disconnect();src.disconnect();mstream.getTracks().forEach(t=>t.stop());await actx.close();}catch{}
 recBtn.textContent='Start recording'; recStatus.textContent='transcribing with all providers…';
 const wav=encodeWav(flatten(bufs),sr); const fd=new FormData();
 fd.append('file',new Blob([wav],{type:'audio/wav'}),'mic.wav');
 fd.append('language',el('liveLang').value); fd.append('reference',el('liveRef').value);
 try{
  const r=await fetch('/api/bench/stt_live',{method:'POST',body:fd}); const j=await r.json(); recStatus.textContent='';
  if(j.error){liveOut.innerHTML='<p class="err">'+esc(j.error)+'</p>';return;}
  const accs=j.rows.map(x=>x.accuracy).filter(v=>v!=null); const best=accs.length?Math.max(...accs):null;
  const fastest=Math.min(...j.rows.map(x=>x.latency_ms).filter(v=>v!=null));
  let h='<table><tr><th>Provider</th>'+(j.has_reference?'<th>Accuracy</th>':'')+'<th>Latency</th><th>Mode</th><th>What it heard</th></tr>';
  for(const x of j.rows){const ac=j.has_reference?`<td class="${x.accuracy===best?'best':''}">${x.accuracy!=null?x.accuracy+'%':'-'}</td>`:'';
   const lc=x.latency_ms===fastest?'best':'';
   h+=`<tr><td>${x.provider}</td>${ac}<td class="${lc}">${x.latency_ms!=null?x.latency_ms+' ms':'-'}</td><td class="meta">${x.mode}</td><td>${esc(x.transcript)}</td></tr>`;}
  h+='</table><p class="meta">'+(j.has_reference?'Green accuracy = best. ':'')+'Green latency = fastest (ignore AssemblyAI latency — batch mode).</p>';
  liveOut.innerHTML=h;
 }catch(e){recStatus.textContent='';liveOut.innerHTML='<p class="err">'+esc(e.message)+'</p>';}
}
function flatten(b){let n=0;b.forEach(x=>n+=x.length);const o=new Float32Array(n);let p=0;b.forEach(x=>{o.set(x,p);p+=x.length;});return o;}
function encodeWav(s,rate){const buf=new ArrayBuffer(44+s.length*2);const v=new DataView(buf);const w=(o,t)=>{for(let i=0;i<t.length;i++)v.setUint8(o+i,t.charCodeAt(i));};
 w(0,'RIFF');v.setUint32(4,36+s.length*2,true);w(8,'WAVE');w(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);
 v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);w(36,'data');v.setUint32(40,s.length*2,true);
 let o=44;for(let i=0;i<s.length;i++){let x=Math.max(-1,Math.min(1,s[i]));v.setInt16(o,x<0?x*0x8000:x*0x7FFF,true);o+=2;}return buf;}
el('ttsBtn').onclick=async()=>{
 el('ttsBtn').disabled=true; el('ttsOut').innerHTML='<p class="meta">measuring…</p>';
 try{
  const r=await fetch('/api/bench/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:el('ttsText').value,lang:el('ttsLang').value})});
  const j=await r.json();
  const oks=j.rows.filter(x=>x.processing_ms!=null).map(x=>x.processing_ms); const best=oks.length?Math.min(...oks):null;
  let h='<table><tr><th>Provider</th><th>Processing latency<br><span class=meta>(network removed)</span></th><th>First-byte<br><span class=meta>(incl. network)</span></th><th>Network RTT</th><th>Listen</th></tr>';
  for(const x of j.rows){const proc=x.processing_ms!=null?x.processing_ms+' ms':'<span class=err>'+x.status+'</span>';const cls=x.processing_ms===best?'best':'';
   h+=`<tr><td>${x.provider}</td><td class="${cls}">${proc}</td><td>${x.first_byte_ms!=null?x.first_byte_ms+' ms':'-'}</td><td>${x.rtt_ms!=null?x.rtt_ms+' ms':'-'}</td><td>${x.audio_b64?`<audio controls src="data:audio/mpeg;base64,${x.audio_b64}"></audio>`:'-'}</td></tr>`;}
  h+='</table><p class="meta">Processing latency = first-byte minus one network round-trip (RTT) = the provider\\'s own speed. Lower = better. Green = fastest.</p>'; el('ttsOut').innerHTML=h;
 }catch(e){el('ttsOut').innerHTML='<p class="err">'+e.message+'</p>';}
 el('ttsBtn').disabled=false;
};
el('sttBtn').onclick=async()=>{
 el('sttBtn').disabled=true; el('sttOut').innerHTML='<p class="meta">generating clip + transcribing… (a few seconds)</p>';
 try{
  const r=await fetch('/api/bench/stt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:el('sttText').value,lang:el('sttLang').value})});
  const j=await r.json();
  if(j.error){el('sttOut').innerHTML='<p class="err">'+esc(j.error)+'</p>';el('sttBtn').disabled=false;return;}
  const best=Math.max(...j.rows.map(x=>x.accuracy));
  let h=`<p class="meta">Test clip: <audio controls src="data:audio/mpeg;base64,${j.clip_b64}"></audio> &nbsp; metric: <b>${j.metric}</b></p>`;
  h+='<table><tr><th>Provider</th><th>Accuracy</th><th>Latency</th><th>Mode</th><th>What it heard</th></tr>';
  for(const x of j.rows){const cls=x.accuracy===best?'best':'';
   h+=`<tr><td>${x.provider}</td><td class="${cls}">${x.accuracy}%</td><td>${x.latency_ms!=null?x.latency_ms+' ms':'-'}</td><td class="meta">${x.mode}</td><td>${esc(x.transcript)}</td></tr>`;}
  h+='</table>'; el('sttOut').innerHTML=h;
 }catch(e){el('sttOut').innerHTML='<p class="err">'+e.message+'</p>';}
 el('sttBtn').disabled=false;
};
</script></body></html>"""


@app.get("/benchmark")
def benchmark_page():
    return render_template_string(BENCH_PAGE)


@app.post("/api/bench/tts")
def api_bench_tts():
    import base64 as _b64
    d = request.get_json(silent=True) or {}
    text = (d.get("text") or "").strip(); lang = d.get("lang", "en")
    if not text:
        return jsonify(rows=[])

    def one(item):
        name, fn = item
        try:
            r = fn(text, lang)
        except Exception as e:
            return {"provider": name, "first_byte_ms": None, "total_ms": None, "status": f"exc:{e}", "audio_b64": None}
        return {"provider": name, "first_byte_ms": r.get("first_byte_ms"),
                "processing_ms": r.get("processing_ms"), "rtt_ms": r.get("rtt_ms"),
                "total_ms": r.get("total_ms"), "status": r.get("status"),
                "audio_b64": (_b64.b64encode(r["audio"]).decode() if r.get("status") == 200 and r.get("audio") else None)}
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(one, bench.TTS_PROVIDERS.items()))
    order = {"Atlas": 0, "ElevenLabs": 1, "Cartesia": 2, "Deepgram": 3}
    rows.sort(key=lambda x: order.get(x["provider"], 9))
    g._detail = "BENCH tts " + lang
    return jsonify(rows=rows)


@app.post("/api/bench/stt")
def api_bench_stt():
    import base64 as _b64
    d = request.get_json(silent=True) or {}
    text = (d.get("text") or "").strip(); lang = d.get("lang", "en")
    if not text:
        return jsonify(error="no text")
    clip = bench.gen_stt_clip(text, lang)
    if not clip:
        return jsonify(error="Could not generate the test clip (check ElevenLabs key/credits on the server).")

    def one(item):
        name, fn = item
        try:
            r = fn(clip, lang)
        except Exception as e:
            r = {"text": "", "latency_ms": None, "mode": "?", "status": f"exc:{e}"}
        score = bench.cer(text, r.get("text") or "") if lang == "ja" else bench.wer(text, r.get("text") or "")
        return {"provider": name, "transcript": r.get("text"), "accuracy": round((1 - score) * 100, 1),
                "latency_ms": r.get("latency_ms"), "mode": r.get("mode")}
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(one, bench.STT_PROVIDERS.items()))
    order = {"Atlas": 0, "ElevenLabs": 1, "Deepgram": 2, "AssemblyAI": 3}
    rows.sort(key=lambda x: order.get(x["provider"], 9))
    g._detail = "BENCH stt " + lang
    return jsonify(clip_b64=_b64.b64encode(clip).decode(), metric=("CER" if lang == "ja" else "WER"), rows=rows)


@app.post("/api/bench/stt_live")
def api_bench_stt_live():
    """Live mic version: transcribe the caller's OWN recorded voice with every STT provider.
    Accuracy is computed only if a 'reference' (what you said) is provided."""
    f = request.files.get("file")
    if not f:
        return jsonify(error="no audio"), 400
    audio = f.read()
    lang = request.form.get("language", "en") or "en"
    reference = (request.form.get("reference") or "").strip()
    STREAMING = {"Atlas", "Deepgram"}   # have realtime APIs -> measure time-to-first-word

    def one(name):
        if name in STREAMING:
            r = bstream.streaming_ttfp(name, audio, lang)
            text = r.get("transcript") or ""
            latency = r.get("ttfp_ms")
            mode = "streaming (time to first word)"
        else:
            try:
                rr = bench.STT_PROVIDERS[name](audio, lang)
            except Exception as e:
                rr = {"text": "", "latency_ms": None, "mode": f"exc:{e}"}
            text = rr.get("text") or ""
            latency = rr.get("latency_ms")
            mode = rr.get("mode")
        row = {"provider": name, "transcript": text, "latency_ms": latency, "mode": mode, "accuracy": None}
        if reference:
            score = bench.cer(reference, text) if lang == "ja" else bench.wer(reference, text)
            row["accuracy"] = round((1 - score) * 100, 1)
        return row
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(one, list(bench.STT_PROVIDERS.keys())))
    order = {"Atlas": 0, "ElevenLabs": 1, "Deepgram": 2, "AssemblyAI": 3}
    rows.sort(key=lambda x: order.get(x["provider"], 9))
    g._detail = "BENCH stt_live " + lang
    return jsonify(metric=("CER" if lang == "ja" else "WER"), has_reference=bool(reference), rows=rows)


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
    # recent turns give memory; keep short to limit tokens/minute on the free LLM tier.
    llm_started = time.time()
    if isinstance(history, list) and history:
        chat = llm.chat(system=ASSISTANT_SYSTEM, history=history[-8:])
    else:
        chat = llm.chat(text, system=ASSISTANT_SYSTEM)
    llm_ended = time.time()
    reply = (chat.get("text") or "").strip()
    if chat.get("status") != 200 or not reply:
        g._detail = f"RESPOND llm_fail status={chat.get('status')}"
        return jsonify(ok=False, stage="llm", status=chat.get("status"),
                       error=chat.get("error") or "LLM returned no text"), 502
    import datetime as _dt
    iso = lambda t: _dt.datetime.utcfromtimestamp(t).strftime("%H:%M:%S.%f")[:-3] + "Z"
    g._detail = (f"RESPOND ok reply={len(reply)}c ttft={chat.get('ttft_ms')}ms "
                 f"llm={chat.get('elapsed_ms')}ms llm_start={iso(llm_started)} llm_end={iso(llm_ended)}")
    return jsonify(ok=True, reply=reply, model=LLM_MODEL,
                   timings={"llm_ttft_ms": chat.get("ttft_ms"),
                            "llm_ms": chat.get("elapsed_ms"),
                            "llm_started": iso(llm_started), "llm_ended": iso(llm_ended)})


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

    # Stream chunks to the browser as Atlas produces them, so playback can start at
    # first audio (low perceived latency). Retry transient errors on the initial call.
    upstream = None
    last = None
    for attempt in range(3):
        try:
            upstream = atlas.speak_stream(text, voice=voice, response_format=fmt)
        except Exception as e:
            last = f"exc:{e}"; time.sleep(0.4 * (attempt + 1)); continue
        if upstream.status_code == 200:
            break
        last = upstream.status_code
        upstream.close()
        if upstream.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(0.5 * (attempt + 1))
            continue
        break
    if upstream is None or upstream.status_code != 200:
        g._detail = f"SPEAK_STREAM fail status={last}"
        return jsonify(ok=False, error=f"atlas-speak returned {last}",
                       detail="Atlas was temporarily unavailable; please try again."), 502
    # Atlas's own Time To First Audio (TTFA) — available from headers immediately.
    ttfa = upstream.headers.get("X-Atlas-First-Audio-Ms")
    g._detail = f"SPEAK_STREAM stream voice={voice} fmt={fmt} chars={len(text)} ttfa={ttfa}ms"

    # STREAM chunks as Atlas produces them, so the browser (MediaSource) can start
    # playing at the first chunk instead of waiting for the whole clip.
    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
    resp = Response(generate(), mimetype=mime)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    if ttfa is not None:
        resp.headers["X-Atlas-First-Audio-Ms"] = str(ttfa)
        resp.headers["Access-Control-Expose-Headers"] = "X-Atlas-First-Audio-Ms"
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
