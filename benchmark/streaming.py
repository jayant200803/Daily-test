"""Real-time streaming STT — measures 'time to first word' (TTFP), the same metric the
speech-to-speech pipeline used. Much faster/fairer than batch transcription for voice agents.

Works on 16-bit PCM mono audio (what the mic records as WAV). Atlas + Deepgram have realtime
WebSocket APIs; we stream the audio and time until the first word comes back.
"""
import asyncio, base64, json, time, io, wave, os
import websockets


def pcm_from_wav(wav_bytes):
    w = wave.open(io.BytesIO(wav_bytes), "rb")
    rate = w.getframerate(); pcm = w.readframes(w.getnframes()); w.close()
    return pcm, rate


async def _connect(url, headers):
    # websockets renamed the header kwarg across versions.
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=None, open_timeout=10)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, max_size=None, open_timeout=10)


async def _atlas(pcm, rate, lang):
    key = os.environ.get("ATLAS_API_KEY", "").strip()
    url = "wss://atlas.oneinbox.ai/v1/realtime?intent=transcription"
    ttfp = None; parts = []
    ws = await _connect(url, {"Authorization": f"Bearer {key}"})
    try:
        await ws.send(json.dumps({"type": "session.update", "session": {"type": "transcription",
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": rate},
            "transcription": {"model": "atlas-listen", "language": lang}}}, "atlas": {"interim": True}}}))
        t0 = time.time(); step = rate * 2 // 10
        for i in range(0, len(pcm), step):
            await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                      "audio": base64.b64encode(pcm[i:i + step]).decode()}))
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=15)); t = m.get("type", "")
            if t.endswith("transcription.interim") or t.endswith("transcription.delta"):
                if ttfp is None: ttfp = int((time.time() - t0) * 1000)
                if t.endswith("delta"): parts.append(m.get("delta", ""))
            elif t.endswith("transcription.completed"):
                if not parts: parts = [m.get("transcript", "")]
                break
            elif t == "error":
                break
    finally:
        await ws.close()
    return ttfp, "".join(parts).strip()


async def _deepgram(pcm, rate, lang):
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    model = "nova-3" if lang == "en" else "nova-2"
    url = (f"wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate={rate}&channels=1"
           f"&model={model}&language={lang}&interim_results=true&punctuate=true")
    ttfp = None; final = ""
    ws = await _connect(url, {"Authorization": f"Token {key}"})
    try:
        t0 = time.time(); step = rate * 2 // 10
        async def send_audio():
            for i in range(0, len(pcm), step):
                await ws.send(pcm[i:i + step])
            await ws.send(json.dumps({"type": "CloseStream"}))
        asyncio.ensure_future(send_audio())
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if m.get("type") == "Results" or "channel" in m:
                alt = (m.get("channel", {}).get("alternatives") or [{}])[0]
                txt = alt.get("transcript", "")
                if txt:
                    if ttfp is None: ttfp = int((time.time() - t0) * 1000)
                    if m.get("is_final"): final = (final + " " + txt).strip()
            if m.get("type") == "Metadata":
                break
    except asyncio.TimeoutError:
        pass
    finally:
        await ws.close()
    return ttfp, final


def streaming_ttfp(provider, wav_bytes, lang):
    """Sync wrapper. provider in {'Atlas','Deepgram'}. Returns {ttfp_ms, transcript}."""
    try:
        pcm, rate = pcm_from_wav(wav_bytes)
    except Exception as e:
        return {"ttfp_ms": None, "transcript": "", "error": f"bad wav: {e}"}
    fn = {"Atlas": _atlas, "Deepgram": _deepgram}.get(provider)
    if not fn:
        return {"ttfp_ms": None, "transcript": ""}
    try:
        ttfp, txt = asyncio.run(fn(pcm, rate, lang))
        return {"ttfp_ms": ttfp, "transcript": txt}
    except Exception as e:
        return {"ttfp_ms": None, "transcript": "", "error": str(e)}
