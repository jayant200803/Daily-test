"""Real-time streaming STT — measures 'time to first word' (TTFP), the same metric the
speech-to-speech pipeline used. Much faster/fairer than batch transcription for voice agents.

Works on 16-bit PCM mono audio (what the mic records as WAV). Atlas + Deepgram have realtime
WebSocket APIs. We stream the audio PACED AT REAL TIME (like a live mic sends ~0.2s chunks as
you speak) and time from the first chunk until the first word comes back. This mirrors the live
speech-to-speech pipeline exactly: audio flows in real time and the first word streams back while
'speaking' is still in progress, so TTFP reflects first-word latency, not whole-file upload time.
"""
import asyncio, base64, json, time, io, wave, os, socket
import websockets

CHUNK_SEC = 0.2  # send 0.2s of audio per step, paced in real time (same as the live mic path)


def _rtt_ms(host, port=443, samples=3):
    """One network round-trip to the host = min TCP-connect time over a few tries.
    Subtracted from the measured latency to leave the engine's own processing time,
    the same 'remove the network' method the TTS benchmark uses."""
    best = None
    for _ in range(samples):
        t = time.time()
        try:
            s = socket.create_connection((host, port), timeout=5); s.close()
        except Exception:
            continue
        ms = int((time.time() - t) * 1000)
        best = ms if best is None else min(best, ms)
    return best


def pcm_from_wav(wav_bytes):
    w = wave.open(io.BytesIO(wav_bytes), "rb")
    rate = w.getframerate(); pcm = w.readframes(w.getnframes()); w.close()
    return pcm, rate


def _finish(first_word_wall, speech_start_wall, t0, rtt):
    """Build the latency numbers the same way the live STS pipeline does:
    - zero point = speech start (lead-in silence excluded); fall back to first chunk sent
    - network removed = subtract one round-trip, leaving the engine's own time."""
    if first_word_wall is None:
        return {"ttfp_ms": None, "ttfp_raw_ms": None, "rtt_ms": rtt}
    # zero point = speech start, but only if it actually precedes the first word. Some engines
    # (e.g. Deepgram) report the speech-start event AFTER the first partial, which would wrongly
    # push the latency toward zero; in that case fall back to the first chunk sent (t0).
    zero = speech_start_wall if (speech_start_wall is not None and speech_start_wall <= first_word_wall) else t0
    from_speech = max(0, int((first_word_wall - zero) * 1000))  # time to first word from speech start
    net_removed = max(0, from_speech - rtt) if rtt is not None else from_speech
    return {"ttfp_ms": net_removed, "ttfp_raw_ms": from_speech, "rtt_ms": rtt}


async def _connect(url, headers):
    # websockets renamed the header kwarg across versions.
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=None, open_timeout=10)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, max_size=None, open_timeout=10)


async def _atlas(pcm, rate, lang):
    key = os.environ.get("ATLAS_API_KEY", "").strip()
    url = "wss://atlas.oneinbox.ai/v1/realtime?intent=transcription"
    rtt = _rtt_ms("atlas.oneinbox.ai")
    first_word_wall = None; speech_start_wall = None; parts = []
    ws = await _connect(url, {"Authorization": f"Bearer {key}"})
    try:
        # interim:True with NO server VAD -> Atlas streams partial hypotheses immediately, so the
        # first word comes back fast. This mirrors the live speech-to-speech demo exactly (that
        # path does not use VAD). VAD was found to DELAY the first word, so it is intentionally off.
        await ws.send(json.dumps({"type": "session.update", "session": {"type": "transcription",
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": rate},
            "transcription": {"model": "atlas-listen", "language": lang}}},
            "atlas": {"interim": True}}}))
        step = rate * 2 // 10   # 0.2s of 16-bit mono audio per chunk
        t0 = time.time()

        async def send_audio():  # stream chunks PACED at real time, not all at once
            for i in range(0, len(pcm), step):
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(pcm[i:i + step]).decode()}))
                await asyncio.sleep(CHUNK_SEC)
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

        sender = asyncio.ensure_future(send_audio())
        try:
            while True:  # read responses concurrently; first word can arrive mid-stream
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=15)); t = m.get("type", "")
                if t.endswith("speech_started"):
                    # zero the clock at speech start (wall time), like STS
                    start_ms = m.get("audio_start_ms")
                    speech_start_wall = t0 + (start_ms / 1000.0) if start_ms is not None else t0
                elif t.endswith("transcription.interim") or t.endswith("transcription.delta"):
                    if first_word_wall is None: first_word_wall = time.time()
                    if t.endswith("delta"): parts.append(m.get("delta", ""))
                elif t.endswith("transcription.completed"):
                    if not parts: parts = [m.get("transcript", "")]
                    break
                elif t == "error":
                    break
        finally:
            sender.cancel()
    finally:
        await ws.close()
    return _finish(first_word_wall, speech_start_wall, t0, rtt), "".join(parts).strip()


async def _deepgram(pcm, rate, lang):
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    model = "nova-3" if lang == "en" else "nova-2"
    # vad_events=true -> Deepgram sends a SpeechStarted event, so we can zero the clock at
    # speech start like STS; network is removed afterwards.
    url = (f"wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate={rate}&channels=1"
           f"&model={model}&language={lang}&interim_results=true&punctuate=true&vad_events=true")
    rtt = _rtt_ms("api.deepgram.com")
    first_word_wall = None; speech_start_wall = None; final = ""
    ws = await _connect(url, {"Authorization": f"Token {key}"})
    try:
        step = rate * 2 // 10   # 0.2s of 16-bit mono audio per chunk
        t0 = time.time()
        async def send_audio():  # stream chunks PACED at real time, not all at once
            for i in range(0, len(pcm), step):
                await ws.send(pcm[i:i + step])
                await asyncio.sleep(CHUNK_SEC)
            await ws.send(json.dumps({"type": "CloseStream"}))
        asyncio.ensure_future(send_audio())
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            mt = m.get("type")
            if mt == "SpeechStarted":
                ts = m.get("timestamp")   # seconds into the stream when speech began
                speech_start_wall = t0 + ts if ts is not None else t0
            elif mt == "Results" or "channel" in m:
                alt = (m.get("channel", {}).get("alternatives") or [{}])[0]
                txt = alt.get("transcript", "")
                if txt:
                    if first_word_wall is None: first_word_wall = time.time()
                    if m.get("is_final"): final = (final + " " + txt).strip()
            if mt == "Metadata":
                break
    except asyncio.TimeoutError:
        pass
    finally:
        await ws.close()
    return _finish(first_word_wall, speech_start_wall, t0, rtt), final


def streaming_ttfp(provider, wav_bytes, lang):
    """Sync wrapper. provider in {'Atlas','Deepgram'}.
    Returns {ttfp_ms (speech-start zero, network removed), ttfp_raw_ms, rtt_ms, transcript}."""
    try:
        pcm, rate = pcm_from_wav(wav_bytes)
    except Exception as e:
        return {"ttfp_ms": None, "transcript": "", "error": f"bad wav: {e}"}
    fn = {"Atlas": _atlas, "Deepgram": _deepgram}.get(provider)
    if not fn:
        return {"ttfp_ms": None, "transcript": ""}
    try:
        info, txt = asyncio.run(fn(pcm, rate, lang))
        return {**info, "transcript": txt}
    except Exception as e:
        return {"ttfp_ms": None, "transcript": "", "error": str(e)}
