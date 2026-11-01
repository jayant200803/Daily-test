"""Build Voice_Benchmark.xlsx from results_full.json: same data as the report, in a
spreadsheet. All numbers measured; parameters explained in plain English on 'Read me'."""
import os, json, statistics
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Voice_Benchmark.xlsx")
HEAD = Font(bold=True, color="FFFFFF"); FILL = PatternFill("solid", fgColor="2F5496")
TITLE = Font(bold=True, size=14); BOLD = Font(bold=True); WRAP = Alignment(wrap_text=True, vertical="top"); CEN = Alignment(horizontal="center")


def hdr(ws):
    for c in ws[1]:
        c.font = HEAD; c.fill = FILL; c.alignment = CEN


def W(ws, w):
    for k, v in w.items():
        ws.column_dimensions[k].width = v


def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def main():
    d = json.load(open(os.path.join(HERE, "results_full.json"), encoding="utf-8"))
    wb = openpyxl.Workbook()

    ws = wb.active; ws.title = "Read me"
    ws["A1"] = "Voice AI Benchmark: Atlas vs the market"; ws["A1"].font = TITLE
    lines = [
        ("", ""),
        ("What this is", "A fair, measured comparison of TTS and STT providers. Same input sent to each; "
                         "measured the same way at the same time. Every number is measured: nothing estimated."),
        ("Generated (UTC)", d.get("generated_at_utc", "")),
        ("Providers", "Atlas, ElevenLabs, Cartesia, Deepgram, AssemblyAI."),
        ("", ""),
        ("--- TTS parameters ---", ""),
        ("WER (correctness)", "The generated audio is transcribed by a neutral recognizer (Deepgram) and compared "
                              "to the input text. WER = (substitutions+insertions+deletions) / words. Lower is better. "
                              "Japanese uses CER (characters), since Japanese has no spaces."),
        ("Naturalness", "How clean/human the voice sounds, from Harmonics-to-Noise Ratio, mapped to 1-5. "
                        "Naturalness = 1 + HNR/7.5 (clamped 1-5). Higher is better."),
        ("Prosody (Hz)", "Expressiveness = standard deviation of pitch. More variation = less robotic. Higher is better."),
        ("Speed (RTF)", "Real-Time Factor = generation time / audio duration. Below 1 is faster than real time; "
                        "near 1 is slow for live agents. Lower is better."),
        ("Robustness", "WER measured only on hard sentences (numbers, currency, symbols, email). Lower is better."),
        ("Time-to-first-audio (ms)", "Time from sending text to first audio received, with the network round-trip "
                                     "subtracted (reflects the provider's own speed). Lower is better."),
        ("", ""),
        ("--- STT parameters ---", ""),
        ("Accuracy (%)", "How close the transcript was to the correct words (100% = perfect). Higher is better."),
        ("Latency (ms)", "For Atlas/Deepgram: streaming time-to-first-word (realtime, the metric for voice agents). "
                         "For AssemblyAI: batch mode, so compare its accuracy, not latency. ElevenLabs STT needs the "
                         "Speech-to-Text key permission (0% = permission off)."),
        ("", ""),
        ("--- Notes ---", ""),
        ("Fairness", "All measured the same way, same time, one machine. Latency is network-removed. The relative "
                     "comparison is the point."),
        ("Deepgram TTS", "English only (Aura). Japanese shows N/A."),
        ("Numbers in sentences", "Hard sentences lower WER for everyone (engines format numbers differently); it's an equal penalty."),
    ]
    r = 3
    for a, b in lines:
        ws.cell(r, 1, a).font = TITLE if a.startswith("---") else (BOLD if a else None)
        ws.cell(r, 2, b).alignment = WRAP
        r += 1
    W(ws, {"A": 30, "B": 98})

    # TTS Summary
    ws = wb.create_sheet("TTS Summary")
    ws.append(["Provider", "Language", "WER (lower=better)", "Naturalness (higher)", "Prosody Hz (higher)",
               "Speed RTF (lower)", "Robustness hard-WER (lower)", "Time-to-first-audio ms (lower)", "Gen time ms"])
    hdr(ws)
    for lang in ("en", "ja"):
        for p in ["Atlas", "ElevenLabs", "Cartesia", "Deepgram"]:
            rs = [x for x in d["tts"] if x["provider"] == p and x["language"] == lang and x.get("rtf") is not None]
            if not rs:
                ws.append([p, lang, "N/A", "", "", "", "", "", ""]); continue
            hard = [x["wer"] for x in rs if x["category"] == "hard" and x["wer"] is not None]
            ws.append([p, lang, avg([x["wer"] for x in rs]), avg([x["naturalness"] for x in rs]),
                       avg([x["prosody_hz"] for x in rs]), avg([x["rtf"] for x in rs]), avg(hard),
                       int(avg([x["ttfa_ms"] for x in rs]) or 0) or "-", int(avg([x["gen_time_ms"] for x in rs]) or 0) or "-"])
    W(ws, {"A": 12, "B": 9, "C": 18, "D": 18, "E": 18, "F": 16, "G": 24, "H": 26, "I": 12})

    # STT Summary
    ws = wb.create_sheet("STT Summary")
    ws.append(["Provider", "Language", "Metric", "Accuracy % (higher)", "Latency ms (lower)", "Mode"])
    hdr(ws)
    for lang in ("en", "ja"):
        for p in ["Atlas", "ElevenLabs", "Deepgram", "AssemblyAI"]:
            rs = [x for x in d["stt"] if x["provider"] == p and x["language"] == lang]
            if not rs:
                continue
            ws.append([p, lang, rs[0]["metric"], avg([x["accuracy_pct"] for x in rs]),
                       int(avg([x["latency_ms"] for x in rs]) or 0) or "-", rs[0]["mode"]])
    W(ws, {"A": 12, "B": 9, "C": 8, "D": 20, "E": 20, "F": 34})

    # TTS raw
    ws = wb.create_sheet("TTS raw")
    ws.append(["Provider", "Lang", "ID", "Category", "Sentence", "WER", "Naturalness", "Prosody Hz", "RTF",
               "TTFA ms", "Gen ms", "Audio s", "Transcript (heard)"])
    hdr(ws)
    for x in d["tts"]:
        ws.append([x["provider"], x["language"], x["id"], x["category"], x["text"], x["wer"], x["naturalness"],
                   x["prosody_hz"], x["rtf"], x["ttfa_ms"], x["gen_time_ms"], x["audio_sec"], x.get("transcript", "")])
    W(ws, {"A": 11, "B": 5, "C": 11, "D": 10, "E": 46, "F": 8, "G": 11, "H": 11, "I": 7, "J": 9, "K": 8, "L": 8, "M": 46})
    for row in ws.iter_rows(min_row=2):
        row[4].alignment = WRAP; row[12].alignment = WRAP

    # STT raw
    ws = wb.create_sheet("STT raw")
    ws.append(["Provider", "Lang", "ID", "Category", "Reference", "Transcript (heard)", "Metric", "Accuracy %", "Latency ms", "Mode"])
    hdr(ws)
    for x in d["stt"]:
        ws.append([x["provider"], x["language"], x["id"], x["category"], x["reference"], x["transcript"],
                   x["metric"], x["accuracy_pct"], x["latency_ms"], x["mode"]])
    W(ws, {"A": 11, "B": 5, "C": 11, "D": 10, "E": 42, "F": 42, "G": 7, "H": 11, "I": 11, "J": 28})
    for row in ws.iter_rows(min_row=2):
        row[4].alignment = WRAP; row[5].alignment = WRAP

    wb.save(OUT)
    print("Saved ->", OUT)


if __name__ == "__main__":
    main()
