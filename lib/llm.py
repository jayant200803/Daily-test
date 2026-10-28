"""LLM client for the Day-2 speech pipeline (the 'reply' step).

Provider-agnostic as long as it's OpenAI-compatible; configured in config.py.
Default = Groq. Kept separate from lib/atlas.py because the Atlas "docs only"
golden rule governs the Atlas calls, not this third-party LLM.
"""
import os
import time
import requests

from config import LLM_BASE_URL, LLM_MODEL, LLM_KEY_ENV


def chat(user_text=None, system=None, model=None, history=None):
    """history: optional list of prior turns [{role:'user'|'assistant', content:str}, ...]
    ending with the latest user turn. If given, it is used instead of user_text so the
    model has the full conversation context (prevents re-asking for known details)."""
    key = os.environ.get(LLM_KEY_ENV)
    if not key:
        return {"status": None, "error": f"{LLM_KEY_ENV} not set on the server"}
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        for m in history:
            role = "assistant" if m.get("role") == "assistant" else "user"
            content = (m.get("content") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
    elif user_text:
        messages.append({"role": "user", "content": user_text})
    body = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 120,   # keep spoken replies short (fast + reliable playback)
        # gpt-oss on Groq is a reasoning model; keep reasoning minimal so the
        # spoken reply comes back fast and 'content' isn't eaten by reasoning tokens.
        "reasoning_effort": "low",
    }
    t0 = time.time()
    try:
        r = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
    except requests.RequestException as e:
        return {"status": None, "error": f"request failed: {e}"}
    out = {"status": r.status_code, "elapsed_ms": int((time.time() - t0) * 1000)}
    try:
        j = r.json()
        if r.ok:
            out["text"] = (j["choices"][0]["message"].get("content") or "").strip()
        else:
            out["error"] = (j.get("error", {}) or {}).get("message") or str(j)[:200]
    except Exception:
        out["error"] = r.text[:300]
    return out
