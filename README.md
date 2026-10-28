# Atlas Day-1 Test Harness

Tests OneInbox Atlas (atlas-listen and atlas-speak) against the Day-1 use cases,
built strictly to the Atlas docs. No endpoints or fields are invented.

## Day-1 use cases covered, and the doc page each follows
| Test | Doc page it follows |
|---|---|
| English accuracy on real terms | Listen > Quickstart + Audio & Languages (language=en, prompt vocabulary) |
| Accents | Listen > Audio & Languages (English audio, same endpoint) |
| Phone-quality audio | Listen > Audio & Languages (8 kHz accepted formats) |
| English quality (TTS) | Speak > Quickstart + Voices |
| Pronunciation dictionary | Speak > Quickstart + Guides > Authentication (dictionary lives on the key) |
| Streaming latency / time to first audio | Speak > Streaming (X-Atlas-First-Audio-Ms) |
| Five parallel, then sixth | Guides > Rate limits and concurrency (429 session_limit) |

## Setup (you only do these two things)
1. `cp .env.example .env` and paste your **test** Atlas key into `ATLAS_API_KEY`. (Secret; never commit .env.)
2. `pip install -r requirements.txt`  (Python 3.9+)

Then run:  `python run_day1.py`

## What runs out of the box vs what needs your clips
- Runs immediately with just the key: auth check, TTS English quality (saves audio to `audio_out/` to listen to), streaming latency / time-to-first-audio, and the 5-parallel concurrency test.
- Needs your audio to score transcription: drop clips into `audio_in/` and list them in `manifest.json` with the correct text in `expected`. Until then those tests show SKIP. This is test data, not something the docs contain.
- Pronunciation test: set the words your key has custom readings for in `config.py` (`PRON_WORDS`). If none, it SKIPs cleanly.

## Reading the results
- PASS / FAIL / CHECK / SKIP / LISTEN / INFO per line.
- Accuracy is shown as a percentage (100% minus word error rate) against your `expected` text.
- TTS quality and pronunciation produce WAV files in `audio_out/` for you to listen to and judge (quality is subjective).
- The concurrency test PASSES when the extra session is refused with `session_limit` (429), exactly as the docs say.

## Live demos (speak-and-test, for showing the team)
These are hands-on tools, separate from the QA harness above. They do **not** score anything —
they let you (or the team) talk to the models directly. Both use the same documented endpoints.

Install the extra deps once: `pip install -r requirements-demo.txt`

### Browser demo — the team just opens a URL (`webapp.py`)
Best for the team: no Python on their side, click and speak.
1. `python webapp.py`
2. It prints a `local` URL and a `on LAN` URL. Open the local one yourself, or share the LAN one
   with teammates on the same Wi‑Fi/network.
3. In the page: click **Start recording**, speak, click **Stop & transcribe** → see what atlas-listen heard.
   Type text and click **Speak it** → hear atlas-speak.

Important about the microphone: browsers only grant mic access on `localhost` or over **HTTPS**.
- On your own machine (`127.0.0.1`) the mic works immediately.
- On the plain `http://` LAN address, other browsers may block the mic. To give the team real
  speak-access, put it behind HTTPS — e.g. run `python webapp.py` and expose it with a tunnel
  like `cloudflared tunnel --url http://localhost:5000` or `ngrok http 5000`, then share the
  `https://…` link. The TTS (type-and-hear) side works over plain http regardless.
- The Atlas test key stays on the server (loaded from `.env`); it is never sent to the browser.
- Flask's built-in server is fine for a demo. For heavier/shared use, front it with a real WSGI
  server (e.g. `waitress-serve --port=5000 webapp:app`).

### Terminal demo — speak from your own machine (`live_demo.py`)
`python live_demo.py` → menu: **1** records your mic (default 30s) and prints the transcript;
**2** types text and plays it back. Uses your machine's mic/speakers only.

## Files
- `run_day1.py` — the runner (run this)
- `webapp.py` — browser speak-and-test demo (optional; needs requirements-demo.txt)
- `live_demo.py` — terminal speak-and-test demo (optional; needs requirements-demo.txt)
- `config.py` — voices, English text, latency target, pronunciation words, concurrency count
- `manifest.json` — your audio clips and their correct transcripts
- `lib/atlas.py` — the Atlas client (endpoints/fields per docs) + accuracy scorer
- `lib/phone.py` — converts a WAV to 8 kHz mono to simulate a phone line
- `audio_in/` — put your clips here · `audio_out/` — generated audio lands here
