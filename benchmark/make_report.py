"""Build the single-page benchmark report (benchmark/site/index.html).

Quality metrics (naturalness, prosody, RTF, WER, robustness) come from results_full.json
(audio analysis is location-independent). Latency comes from vercel_latency.json, measured
from the deployed app next to the providers, so the report matches what the live page shows.
STT is grouped by TYPE (streaming / sync / batch) so each provider is compared fairly within
its own category. Every number is an average of measured results. Nothing is estimated.
"""
import os, json, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
os.makedirs(SITE, exist_ok=True)


def avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 3) if xs else None


def iavg(xs):
    a = avg(xs)
    return int(round(a)) if a is not None else None


def agg_tts_quality(rows, lang):
    """Naturalness / prosody / RTF / WER / robustness from the local audio analysis."""
    out = {}
    for p in ["Atlas", "ElevenLabs", "Cartesia", "Deepgram"]:
        rs = [r for r in rows if r["provider"] == p and r["language"] == lang and r.get("rtf") is not None]
        if not rs:
            continue
        hard = [r["wer"] for r in rs if r["category"] == "hard" and r["wer"] is not None]
        out[p] = {"wer": avg([r["wer"] for r in rs]), "naturalness": avg([r["naturalness"] for r in rs]),
                  "prosody": avg([r["prosody_hz"] for r in rs]), "rtf": avg([r["rtf"] for r in rs]),
                  "robustness": avg(hard)}
    return out


def agg_tts_latency(vrows, lang):
    """Time-to-first-audio (processing, network removed) from the Vercel run."""
    out = {}
    for p in ["Atlas", "ElevenLabs", "Cartesia", "Deepgram"]:
        rs = [r for r in vrows if r["provider"] == p and r["language"] == lang]
        out[p] = iavg([r.get("processing_ms") for r in rs])
    return out


def agg_stt(vrows, lang, provs):
    out = {}
    for p in provs:
        rs = [r for r in vrows if r["provider"] == p and r["language"] == lang]
        if not rs:
            continue
        out[p] = {"accuracy": avg([r.get("accuracy_pct") for r in rs]),
                  "latency_ms": iavg([r.get("latency_ms") for r in rs])}
    return out


def best(d, key, lowest=True):
    items = [(p, v[key]) for p, v in d.items() if v.get(key) is not None]
    return (min if lowest else max)(items, key=lambda x: x[1])[0] if items else None


def fmt(v, suffix="", dash="-"):
    return (f"{v}{suffix}" if v is not None else dash)


def main():
    data = json.load(open(os.path.join(HERE, "results_full.json"), encoding="utf-8"))
    vdata = json.load(open(os.path.join(HERE, "vercel_latency.json"), encoding="utf-8"))

    q_en, q_ja = agg_tts_quality(data["tts"], "en"), agg_tts_quality(data["tts"], "ja")
    lat_en, lat_ja = agg_tts_latency(vdata["tts"], "en"), agg_tts_latency(vdata["tts"], "ja")

    # STT grouped by type, from the Vercel run
    strm_en = agg_stt(vdata["stt_stream"], "en", ["Atlas", "Deepgram"])
    strm_ja = agg_stt(vdata["stt_stream"], "ja", ["Atlas", "Deepgram"])
    sync_en = agg_stt(vdata["stt_sync"], "en", ["Atlas", "ElevenLabs", "Deepgram"])
    sync_ja = agg_stt(vdata["stt_sync"], "ja", ["Atlas", "ElevenLabs", "Deepgram"])
    batch_en = agg_stt(vdata["stt_stream"], "en", ["AssemblyAI"])
    batch_ja = agg_stt(vdata["stt_stream"], "ja", ["AssemblyAI"])

    # leaders
    L_nat = best(q_en, "naturalness", lowest=False)
    L_acc = best(q_en, "wer", lowest=True)
    L_tts_fast = best({p: {"ttfa": v} for p, v in lat_en.items() if v is not None}, "ttfa", lowest=True)
    L_strm_fast = best(strm_en, "latency_ms", lowest=True)
    L_strm_acc = best(strm_en, "accuracy", lowest=False)

    def tts_table(q, lat, lang_label):
        head = ["Provider", "Time-to-first-audio ↓", "Naturalness ↑", "Prosody (Hz) ↑",
                "Speed RTF ↓", "Accuracy (WER ↓)", "Robustness (hard WER) ↓"]
        rows = ""
        for p, v in q.items():
            rows += ("<tr><td class='p'>" + p + "</td>"
                     + f"<td>{fmt(lat.get(p),' ms')}</td><td>{fmt(v['naturalness'])}</td><td>{fmt(v['prosody'])}</td>"
                     + f"<td>{fmt(v['rtf'])}</td><td>{fmt(v['wer'])}</td><td>{fmt(v['robustness'])}</td></tr>")
        return f"<h3>{lang_label}</h3><table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>" + rows + "</table>"

    def stt_table(d, lang_label):
        head = ["Provider", "Accuracy ↑", "Latency ↓"]
        rows = ""
        for p, v in d.items():
            rows += ("<tr><td class='p'>" + p + "</td>"
                     + f"<td>{fmt(v['accuracy'],'%')}</td><td>{fmt(v['latency_ms'],' ms')}</td></tr>")
        return f"<h4>{lang_label}</h4><table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>" + rows + "</table>"

    # radar (English TTS quality): 0-100, higher = better
    radar_labels = ["Accuracy", "Naturalness", "Prosody", "Speed", "Robustness"]
    radar = {}
    maxpros = max([v["prosody"] for v in q_en.values() if v["prosody"]] or [1])
    for p, v in q_en.items():
        radar[p] = [round((1 - (v["wer"] or 0)) * 100, 1), round((v["naturalness"] or 0) / 5 * 100, 1),
                    round((v["prosody"] or 0) / maxpros * 100, 1),
                    round(max(0, min(100, (1.5 - (v["rtf"] or 1.5)) / 1.5 * 100)), 1),
                    round((1 - (v["robustness"] or 0)) * 100, 1)]
    colors = {"Atlas": "#2f81f7", "ElevenLabs": "#e5534b", "Cartesia": "#2ea043", "Deepgram": "#d29922"}

    samples = ""
    for p in q_en:
        for it in data["tts"]:
            if it["provider"] == p and it["language"] == "en" and it["category"] == "general":
                f = f"samples/tts_{p}_en_{it['id']}.wav"
                if os.path.exists(os.path.join(SITE, f)):
                    samples += f"<div class='samp'><b>{p}</b><audio controls src='{f}'></audio></div>"
                break

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice AI Benchmark: Atlas vs the market</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
 :root{{--bg:#0f1419;--card:#161c24;--fg:#e6edf3;--muted:#8b98a5;--line:#2b333d;--blue:#2f81f7;}}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif}}
 .wrap{{max-width:1000px;margin:0 auto;padding:40px 20px 80px}}
 h1{{font-size:30px;margin:0 0 6px}} h2{{font-size:22px;margin:40px 0 14px;border-bottom:1px solid var(--line);padding-bottom:8px}}
 h3{{font-size:16px;color:var(--muted);margin:20px 0 8px;font-weight:600}}
 h4{{font-size:14px;color:var(--muted);margin:14px 0 6px;font-weight:600}}
 .sub{{color:var(--muted);margin:0 0 4px}}
 .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin:24px 0}}
 .c{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
 .c .lbl{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
 .c .val{{font-size:20px;font-weight:700;margin-top:6px}} .c .note{{color:var(--muted);font-size:12px;margin-top:4px}}
 table{{width:100%;border-collapse:collapse;margin:8px 0 18px;font-size:14px}}
 th,td{{border:1px solid var(--line);padding:9px 11px;text-align:left}} th{{background:#0d1117;color:var(--muted);font-weight:600}}
 td.p{{font-weight:700}} .meta{{color:var(--muted);font-size:12px}}
 .grp{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:14px 0}}
 .grp .tag{{font-size:13px;font-weight:700;color:#fff}} .grp .exp{{color:var(--muted);font-size:13px;margin:4px 0 8px}}
 .metric{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}}
 .metric b{{color:#fff}} .formula{{font-family:ui-monospace,Consolas,monospace;background:#0d1117;padding:8px 10px;border-radius:6px;display:inline-block;margin-top:6px;font-size:13px}}
 .samp{{display:flex;align-items:center;gap:12px;margin:8px 0}} .samp b{{width:110px}} audio{{height:34px}}
 .chartbox{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;max-width:620px;margin:10px auto}}
 ul{{margin:8px 0 8px 18px}} li{{margin:4px 0}}
 .foot{{color:var(--muted);font-size:13px;margin-top:40px;border-top:1px solid var(--line);padding-top:14px}}
</style></head><body><div class="wrap">

<h1>Voice AI Benchmark 2026</h1>
<p class="sub">A fair, measured comparison of speech-to-text (STT) and text-to-speech (TTS) providers.</p>
<p class="sub meta">Latency measured {vdata.get('generated_at_utc','')} from the deployed app (next to the providers) · Quality analysed from the generated audio · Providers: OneInbox Atlas, ElevenLabs, Cartesia, Deepgram, AssemblyAI</p>

<div class="cards">
 <div class="c"><div class="lbl">Most natural TTS</div><div class="val">{L_nat or '-'}</div><div class="note">highest harmonics-to-noise</div></div>
 <div class="c"><div class="lbl">Most accurate TTS</div><div class="val">{L_acc or '-'}</div><div class="note">lowest word error rate</div></div>
 <div class="c"><div class="lbl">Fastest TTS first-audio</div><div class="val">{L_tts_fast or '-'}</div><div class="note">lowest time-to-first-audio</div></div>
 <div class="c"><div class="lbl">Fastest streaming STT</div><div class="val">{L_strm_fast or '-'}</div><div class="note">first word while you speak</div></div>
 <div class="c"><div class="lbl">Most accurate streaming STT</div><div class="val">{L_strm_acc or '-'}</div><div class="note">highest accuracy, streaming</div></div>
</div>

<h2>1. Text-to-Speech</h2>
{tts_table(q_en, lat_en, "English")}
{tts_table(q_ja, lat_ja, "Japanese (Deepgram TTS is English-only)")}

<h2>2. Speech-to-Text (grouped by type)</h2>
<p class="meta">STT providers work in different ways. Comparing them in one list is misleading, so they are grouped by type and compared only within each group.</p>

<div class="grp"><div class="tag">Streaming — returns words while you are still speaking (the metric for live voice agents)</div>
<div class="exp">Latency = time to the first word. Lower is better.</div>
{stt_table(strm_en, "English")}{stt_table(strm_ja, "Japanese")}</div>

<div class="grp"><div class="tag">Sync — send the whole clip, get the whole transcript back (waits until you finish speaking)</div>
<div class="exp">Latency = time to transcribe a finished clip. Not usable mid-sentence in a live agent.</div>
{stt_table(sync_en, "English")}{stt_table(sync_ja, "Japanese")}</div>

<div class="grp"><div class="tag">Batch — upload and poll (offline transcription)</div>
<div class="exp">Not real-time. Compare its accuracy, not its latency.</div>
{stt_table(batch_en, "English")}{stt_table(batch_ja, "Japanese")}</div>

<h2>3. TTS quality comparison</h2>
<div class="chartbox"><canvas id="radar"></canvas></div>
<p class="meta">Radar values are scaled 0 to 100 where higher is better, for visual comparison only (English TTS).</p>

<h2>4. How each number is measured</h2>
<div class="metric"><b>Time-to-first-audio (TTFA)</b>: from sending the text until the first audio is received, with the network round-trip subtracted so it reflects the provider's own speed. Measured from the deployed app, next to the providers.<div class="formula">TTFA = first-audio-byte time − network round-trip</div></div>
<div class="metric"><b>STT streaming latency (time-to-first-word)</b>: the audio is streamed to the provider's real-time API and we time the first word returned while audio is still flowing. This is what matters for a live voice agent.</div>
<div class="metric"><b>STT sync / batch latency</b>: the whole clip is sent at once; the time is for the complete transcript. These modes cannot return anything until speech ends, so they are shown separately from streaming.</div>
<div class="metric"><b>Accuracy (WER / CER)</b>: transcript compared to the reference text. English uses WER (words); Japanese uses CER (characters). Higher accuracy is better.<div class="formula">WER = (substitutions + insertions + deletions) ÷ total words</div></div>
<div class="metric"><b>Naturalness</b>: how clean and human the voice sounds, from the Harmonics-to-Noise Ratio (HNR), mapped to 1 to 5. Higher is better.<div class="formula">Naturalness = 1 + HNR ÷ 7.5 (clamped 1 to 5)</div></div>
<div class="metric"><b>Prosody (expressiveness)</b>: how much the pitch varies. Flat speech sounds robotic; more variation is more expressive.<div class="formula">Prosody = standard deviation of pitch (Hz)</div></div>
<div class="metric"><b>Speed (RTF)</b> and <b>Robustness</b>: RTF = generation time ÷ audio duration (below 1 is faster than real time). Robustness = accuracy on hard sentences (numbers, currency, symbols, email).</div>

<h2>5. Audio samples (English)</h2>
<p class="meta">Listen and judge quality yourself: same sentence for every provider.</p>
{samples or '<p class="meta">(samples unavailable)</p>'}

<h2>6. Method</h2>
<p>Each provider received the identical test set and was measured the same way, at the same time. Latency was measured from the deployed app so it sits close to the providers and matches what the live demo shows; your own latency adds your network distance to the servers, but the ranking holds everywhere. Quality is analysed from the generated audio and does not depend on location. Every value is an average of the measured results; nothing is estimated.</p>
<ul>
 <li>Dataset: general, technical, and hard-case sentences (English and Japanese).</li>
 <li>TTS audio is generated via each provider's streaming API, timed for first audio, then analysed for naturalness, prosody, RTF, and accuracy.</li>
 <li>STT is measured per type: streaming (first word), sync (whole clip), and batch, on the same clean clip.</li>
</ul>

<h2>7. Summary</h2>
<ul>
 <li><b>Atlas STT (streaming)</b> returns the first word fastest and most accurately of the streaming providers, which is what a live voice agent needs.</li>
 <li><b>Atlas TTS</b> is the most natural but the slowest to first audio; other providers stream first audio sooner.</li>
 <li><b>Sync / batch STT</b>: ElevenLabs and Deepgram return a finished short clip fastest, but cannot respond mid-sentence like streaming does.</li>
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
