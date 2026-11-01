"""Build the single-page benchmark report (benchmark/site/index.html) from results_full.json.
Every number is averaged from the measured results. Plain, factual English. No invented data."""
import os, json, statistics, html

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
os.makedirs(SITE, exist_ok=True)


def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def agg_tts(rows, lang):
    out = {}
    for p in ["Atlas", "ElevenLabs", "Cartesia", "Deepgram"]:
        rs = [r for r in rows if r["provider"] == p and r["language"] == lang and r.get("rtf") is not None]
        if not rs:
            continue
        hard = [r["wer"] for r in rs if r["category"] == "hard" and r["wer"] is not None]
        out[p] = {
            "wer": avg([r["wer"] for r in rs]),
            "naturalness": avg([r["naturalness"] for r in rs]),
            "prosody": avg([r["prosody_hz"] for r in rs]),
            "rtf": avg([r["rtf"] for r in rs]),
            "ttfa_ms": int(avg([r["ttfa_ms"] for r in rs]) or 0) or None,
            "gen_ms": int(avg([r["gen_time_ms"] for r in rs]) or 0) or None,
            "robustness": avg(hard),
        }
    return out


def agg_stt(rows, lang):
    out = {}
    for p in ["Atlas", "ElevenLabs", "Deepgram", "AssemblyAI"]:
        rs = [r for r in rows if r["provider"] == p and r["language"] == lang]
        if not rs:
            continue
        out[p] = {"accuracy": avg([r["accuracy_pct"] for r in rs]),
                  "latency_ms": int(avg([r["latency_ms"] for r in rs]) or 0) or None,
                  "mode": rs[0]["mode"], "metric": rs[0]["metric"]}
    return out


def best(d, key, lowest=True, exclude_none=True):
    items = [(p, v[key]) for p, v in d.items() if v.get(key) is not None]
    if not items:
        return None
    return (min if lowest else max)(items, key=lambda x: x[1])[0]


def fmt(v, suffix="", dash="—"):
    return (f"{v}{suffix}" if v is not None else dash)


def main():
    data = json.load(open(os.path.join(HERE, "results_full.json"), encoding="utf-8"))
    tts_en = agg_tts(data["tts"], "en")
    tts_ja = agg_tts(data["tts"], "ja")
    stt_en = agg_stt(data["stt"], "en")
    stt_ja = agg_stt(data["stt"], "ja")

    # leaders (English TTS)
    L_acc = best(tts_en, "wer", lowest=True)
    L_fast = best(tts_en, "rtf", lowest=True)
    L_nat = best(tts_en, "naturalness", lowest=False)
    L_robust = best(tts_en, "robustness", lowest=True)
    L_stt_fast = best({k: v for k, v in stt_en.items() if v["mode"].startswith("streaming")}, "latency_ms", lowest=True)
    L_stt_acc = best(stt_en, "accuracy", lowest=False)

    def tts_table(d, lang_label):
        head = ["Provider", "WER ↓", "Naturalness ↑", "Prosody (Hz) ↑", "Speed RTF ↓", "Robustness (hard WER) ↓", "Time-to-first-audio ↓"]
        rows = ""
        for p, v in d.items():
            rows += ("<tr><td class='p'>" + p + "</td>"
                     + f"<td>{fmt(v['wer'])}</td><td>{fmt(v['naturalness'])}</td><td>{fmt(v['prosody'])}</td>"
                     + f"<td>{fmt(v['rtf'])}</td><td>{fmt(v['robustness'])}</td><td>{fmt(v['ttfa_ms'],' ms')}</td></tr>")
        return f"<h3>{lang_label}</h3><table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>" + rows + "</table>"

    def stt_table(d, lang_label):
        head = ["Provider", "Accuracy ↑", "Latency ↓", "Mode"]
        rows = ""
        for p, v in d.items():
            rows += ("<tr><td class='p'>" + p + "</td>"
                     + f"<td>{fmt(v['accuracy'],'%')}</td><td>{fmt(v['latency_ms'],' ms')}</td><td class='meta'>{v['mode']}</td></tr>")
        return f"<h3>{lang_label} ({list(d.values())[0]['metric'] if d else ''})</h3><table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>" + rows + "</table>"

    # radar data (English TTS) — 0-100, higher = better
    radar_labels = ["Accuracy", "Naturalness", "Prosody", "Speed", "Robustness"]
    radar = {}
    maxpros = max([v["prosody"] for v in tts_en.values() if v["prosody"]] or [1])
    for p, v in tts_en.items():
        radar[p] = [
            round((1 - (v["wer"] or 0)) * 100, 1),
            round((v["naturalness"] or 0) / 5 * 100, 1),
            round((v["prosody"] or 0) / maxpros * 100, 1),
            round(max(0, min(100, (1.5 - (v["rtf"] or 1.5)) / 1.5 * 100)), 1),
            round((1 - (v["robustness"] or 0)) * 100, 1),
        ]
    colors = {"Atlas": "#2f81f7", "ElevenLabs": "#e5534b", "Cartesia": "#2ea043", "Deepgram": "#d29922"}

    # audio samples (English general)
    samples = ""
    for p in tts_en:
        for it in data["tts"]:
            if it["provider"] == p and it["language"] == "en" and it["category"] == "general":
                f = f"samples/tts_{p}_en_{it['id']}.wav"
                if os.path.exists(os.path.join(SITE, f)):
                    samples += f"<div class='samp'><b>{p}</b><audio controls src='{f}'></audio></div>"
                break

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice AI Benchmark — Atlas vs the market</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
 :root{{--bg:#0f1419;--card:#161c24;--fg:#e6edf3;--muted:#8b98a5;--line:#2b333d;--blue:#2f81f7;}}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif}}
 .wrap{{max-width:1000px;margin:0 auto;padding:40px 20px 80px}}
 h1{{font-size:30px;margin:0 0 6px}} h2{{font-size:22px;margin:40px 0 14px;border-bottom:1px solid var(--line);padding-bottom:8px}}
 h3{{font-size:16px;color:var(--muted);margin:20px 0 8px;font-weight:600}}
 .sub{{color:var(--muted);margin:0 0 4px}}
 .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin:24px 0}}
 .c{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
 .c .lbl{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
 .c .val{{font-size:20px;font-weight:700;margin-top:6px}} .c .note{{color:var(--muted);font-size:12px;margin-top:4px}}
 table{{width:100%;border-collapse:collapse;margin:8px 0 18px;font-size:14px}}
 th,td{{border:1px solid var(--line);padding:9px 11px;text-align:left}} th{{background:#0d1117;color:var(--muted);font-weight:600}}
 td.p{{font-weight:700}} .meta{{color:var(--muted);font-size:12px}}
 .metric{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}}
 .metric b{{color:#fff}} .formula{{font-family:ui-monospace,Consolas,monospace;background:#0d1117;padding:8px 10px;border-radius:6px;display:inline-block;margin-top:6px;font-size:13px}}
 .samp{{display:flex;align-items:center;gap:12px;margin:8px 0}} .samp b{{width:110px}} audio{{height:34px}}
 .chartbox{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;max-width:620px;margin:10px auto}}
 ul{{margin:8px 0 8px 18px}} li{{margin:4px 0}}
 .foot{{color:var(--muted);font-size:13px;margin-top:40px;border-top:1px solid var(--line);padding-top:14px}}
</style></head><body><div class="wrap">

<h1>Voice AI Benchmark 2026</h1>
<p class="sub">A fair, measured comparison of speech-to-text (STT) and text-to-speech (TTS) providers.</p>
<p class="sub meta">Generated {data.get('generated_at_utc','')} · Providers: OneInbox Atlas, ElevenLabs, Cartesia, Deepgram, AssemblyAI</p>

<div class="cards">
 <div class="c"><div class="lbl">Fastest TTS (Speed / RTF)</div><div class="val">{L_fast or '—'}</div><div class="note">lowest real-time factor</div></div>
 <div class="c"><div class="lbl">Most accurate TTS (WER)</div><div class="val">{L_acc or '—'}</div><div class="note">lowest word error rate</div></div>
 <div class="c"><div class="lbl">Most natural TTS</div><div class="val">{L_nat or '—'}</div><div class="note">highest harmonics-to-noise</div></div>
 <div class="c"><div class="lbl">Fastest STT (streaming)</div><div class="val">{L_stt_fast or '—'}</div><div class="note">lowest time-to-first-word</div></div>
 <div class="c"><div class="lbl">Most accurate STT</div><div class="val">{L_stt_acc or '—'}</div><div class="note">highest transcription accuracy</div></div>
</div>

<h2>1. Text-to-Speech leaderboard</h2>
{tts_table(tts_en, "English")}
{tts_table(tts_ja, "Japanese (Deepgram TTS is English-only)")}

<h2>2. Speech-to-Text leaderboard</h2>
{stt_table(stt_en, "English")}
{stt_table(stt_ja, "Japanese")}

<h2>3. TTS metric comparison</h2>
<div class="chartbox"><canvas id="radar"></canvas></div>
<p class="meta">Radar values are scaled 0–100 where higher is better, for visual comparison only (English TTS).</p>

<h2>4. How each number is measured</h2>
<div class="metric"><b>Time-to-first-audio (TTFA)</b> — how long from sending the text until the first audio is received, with the network round-trip subtracted so it reflects the provider's own speed.<div class="formula">TTFA = first-audio-byte time − network round-trip</div></div>
<div class="metric"><b>Full generation time</b> — how long until the whole audio clip is produced.</div>
<div class="metric"><b>Speed (RTF, Real-Time Factor)</b> — generation time divided by how long the audio plays. Below 1 is faster than real time; near 1 is slow for live use.<div class="formula">RTF = generation time ÷ audio duration</div></div>
<div class="metric"><b>Correctness (WER)</b> — the generated audio is transcribed by a neutral speech recognizer (Deepgram) and compared to the input text. Lower is better.<div class="formula">WER = (substitutions + insertions + deletions) ÷ total words</div><span class="meta">Japanese uses CER (character error rate), since Japanese has no spaces between words.</span></div>
<div class="metric"><b>Naturalness</b> — how clean and human the voice sounds, from the Harmonics-to-Noise Ratio (HNR) of the audio, mapped to a 1–5 score. Higher is better.<div class="formula">Naturalness = 1 + HNR ÷ 7.5 (clamped 1–5)</div></div>
<div class="metric"><b>Prosody (expressiveness)</b> — how much the pitch varies. Flat speech sounds robotic; more variation is more expressive.<div class="formula">Prosody = standard deviation of pitch (Hz)</div></div>
<div class="metric"><b>Robustness</b> — correctness (WER) measured only on hard sentences (numbers, currency, symbols, email). Lower is better.</div>
<div class="metric"><b>STT latency (time-to-first-word)</b> — the audio is streamed to the provider's real-time API and we time the first word. This is the metric that matters for live voice agents. AssemblyAI runs in batch mode, so compare its accuracy, not its latency.</div>

<h2>5. Audio samples (English)</h2>
<p class="meta">Listen and judge quality yourself — same sentence for every provider.</p>
{samples or '<p class="meta">(samples unavailable)</p>'}

<h2>6. Method</h2>
<p>Each provider received the identical test set and was measured the same way, at the same time, from one machine. Latency is reported with the network removed so it reflects provider speed rather than our location. Every value on this page is an average of the measured results; nothing is estimated.</p>
<ul>
 <li>Dataset: general, technical, and hard-case sentences (English and Japanese).</li>
 <li>TTS audio is generated via each provider's API, then analysed for duration, RTF, naturalness, prosody, and transcribed for correctness.</li>
 <li>STT uses each provider's real-time streaming API (where available) on the same clean clip.</li>
</ul>

<h2>7. Summary</h2>
<ul>
 <li><b>Atlas STT</b> is fast (streaming time-to-first-word) and strong in Japanese.</li>
 <li><b>Atlas TTS</b> is the slowest to first audio and runs near real time (RTF ≈ 1), which is a limitation for live voice agents.</li>
 <li><b>Cartesia and ElevenLabs</b> lead on TTS speed; <b>Deepgram</b> is a strong all-rounder.</li>
</ul>

<p class="foot">All measurements performed live via each provider's public API. This page can be re-generated with the same scripts under the same conditions.</p>
</div>
<script>
const radarData = {json.dumps(radar)};
const colors = {json.dumps(colors)};
const ds = Object.keys(radarData).map(p => ({{label:p, data:radarData[p], borderColor:colors[p]||'#888',
  backgroundColor:(colors[p]||'#888')+'22', borderWidth:2, pointRadius:2}}));
new Chart(document.getElementById('radar'), {{type:'radar',
  data:{{labels:{json.dumps(radar_labels)}, datasets:ds}},
  options:{{plugins:{{legend:{{labels:{{color:'#e6edf3'}}}}}},
    scales:{{r:{{angleLines:{{color:'#2b333d'}},grid:{{color:'#2b333d'}},
      pointLabels:{{color:'#8b98a5'}},ticks:{{display:false}},suggestedMin:0,suggestedMax:100}}}}}}}});
</script>
</body></html>"""
    open(os.path.join(SITE, "index.html"), "w", encoding="utf-8").write(page)
    print("Saved ->", os.path.join(SITE, "index.html"))


if __name__ == "__main__":
    main()
