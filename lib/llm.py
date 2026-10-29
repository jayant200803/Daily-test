"""LLM client for the Day-2 speech pipeline (the 'reply' step).

Provider-agnostic as long as it's OpenAI-compatible; configured in config.py.
Default = Groq. Kept separate from lib/atlas.py because the Atlas "docs only"
golden rule governs the Atlas calls, not this third-party LLM.
"""
import os
import re
import time
import json
import requests

from config import LLM_BASE_URL, LLM_MODEL, LLM_KEY_ENV


def _clean_reply(text):
    """Guard against the model mashing several turns/questions into one reply.
    Keep the first clean line: de-duplicate repeated sentences and stop after the
    first question (a voice agent asks one thing at a time), max 2 sentences."""
    text = (text or "").strip()
    if not text:
        return text
    # ensure a space after sentence punctuation (fixes "depart?When" -> "depart? When")
    text = re.sub(r'([.?!])(?=[A-Za-z0-9])', r'\1 ', text)
    parts = re.findall(r'[^.?!]*[.?!]', text)   # complete sentences only
    if not parts:
        return text                              # no punctuation -> leave as-is
    out, seen = [], set()
    for p in parts:
        s = p.strip()
        key = s.lower()
        if not s or key in seen:
            continue
        seen.add(key)
        out.append(s)
        if s.endswith('?') or len(out) >= 3:     # stop after the first question; max 3 sentences
            break
    return " ".join(out).strip() or text


def _stream_once(key, body):
    """One streamed chat call. Returns {status, ttft_ms, elapsed_ms, text} or {status, error}."""
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
            "ttft_ms": ttft_ms, "text": "".join(parts)}


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
    # gpt-oss is a reasoning model: it spends tokens "thinking" before the reply, so the
    # cap must cover reasoning + content (too low -> empty 'content'). The cap does NOT
    # lengthen the spoken reply (the prompt + _clean_reply control that).
    body = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 600,       # room for reasoning + a short reply, while saving TPM
        "reasoning_effort": "low",
        "stream": True,          # stream so we can measure Time To First Token (TTFT)
    }

    def _is_rate_limit(o):
        e = (o.get("error") or "").lower()
        return o.get("status") == 429 or "rate limit" in e or "tokens per minute" in e

    out = _stream_once(key, body)
    if _is_rate_limit(out):
        return {"status": 429, "error": "The assistant is briefly busy (rate limit). Please try again in a few seconds."}

    # Empty content (reasoning ate the budget) -> one retry with a bigger budget, unless
    # that retry is rate-limited. Only then fall back to a safe line.
    if out.get("status") == 200 and not (out.get("text") or "").strip():
        retry = _stream_once(key, {**body, "max_tokens": 900})
        if _is_rate_limit(retry):
            return {"status": 429, "error": "The assistant is briefly busy (rate limit). Please try again in a few seconds."}
        if retry.get("status") == 200 and (retry.get("text") or "").strip():
            out = retry
        else:
            out["text"] = "I'm sorry, could you please say that again?"
    if out.get("status") == 200:
        out["text"] = _clean_reply(out.get("text", ""))
    return out
