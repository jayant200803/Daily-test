"""Tiny OpenAI chat client for the Day-2 speech pipeline.

Separate from lib/atlas.py on purpose: the Atlas "docs only" golden rule governs
the Atlas calls; the LLM in the middle is a different provider (OpenAI).
Uses plain requests (no SDK dependency) against the documented Chat Completions API.
"""
import os
import time
import requests

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def chat(user_text, system=None, model="gpt-4o-mini", temperature=0.4):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return {"status": None, "error": "OPENAI_API_KEY not set on the server"}
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_text})
    t0 = time.time()
    try:
        r = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "temperature": temperature},
            timeout=60,
        )
    except requests.RequestException as e:
        return {"status": None, "error": f"request failed: {e}"}
    out = {"status": r.status_code, "elapsed_ms": int((time.time() - t0) * 1000)}
    try:
        j = r.json()
        if r.ok:
            out["text"] = j["choices"][0]["message"]["content"]
        else:
            out["error"] = j.get("error", {}).get("message") or str(j)[:200]
    except Exception:
        out["error"] = r.text[:300]
    return out
