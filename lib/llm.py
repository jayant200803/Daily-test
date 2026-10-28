"""LLM client for the Day-2 speech pipeline (the 'reply' step).

Provider-agnostic as long as it's OpenAI-compatible; configured in config.py.
Default = Groq. Kept separate from lib/atlas.py because the Atlas "docs only"
golden rule governs the Atlas calls, not this third-party LLM.
"""
import os
import time
import json
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
        # Enough room for this reasoning model to think AND emit the reply (too low
        # starves 'content' -> empty text). Reply length is controlled by the prompt,
        # not this cap, so a larger value does NOT make the spoken reply longer.
        "max_tokens": 256,
        # gpt-oss on Groq is a reasoning model; keep reasoning minimal so the
        # spoken reply comes back fast and 'content' isn't eaten by reasoning tokens.
        "reasoning_effort": "low",
        "stream": True,          # stream so we can measure Time To First Token (TTFT)
    }
    t0 = time.time()
    ttft_ms = None
    parts = []
    try:
        with requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"},
            json=body, stream=True, timeout=(5, 60),
        ) as r:
            if r.status_code != 200:
                try:
                    err = (r.json().get("error", {}) or {}).get("message")
                except Exception:
                    err = r.text[:200]
                return {"status": r.status_code, "error": err or f"HTTP {r.status_code}"}
            for line in r.iter_lines():
                if not line or not line.startswith(b"data: "):
                    continue
                data = line[6:]
                if data == b"[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except Exception:
                    continue
                piece = delta.get("content")
                if piece:
                    if ttft_ms is None:          # first content token -> TTFT
                        ttft_ms = int((time.time() - t0) * 1000)
                    parts.append(piece)
    except requests.RequestException as e:
        return {"status": None, "error": f"request failed: {e}"}
    return {"status": 200, "elapsed_ms": int((time.time() - t0) * 1000),
            "ttft_ms": ttft_ms, "text": "".join(parts).strip()}
