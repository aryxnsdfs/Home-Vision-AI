"""Resilient JSON LLM client: Gemini key rotation with an OpenAI fallback.

Every architectural stage in :mod:`cloud_extractor` used to build its own
``genai.Client`` around a single ``GEMINI_API_KEY`` and had no retry.  One
quota error, one 503, one dropped connection and the stage returned an empty
program, which the pipeline then rendered as a generic fallback house.

This module centralises those calls so a stage gets:

  * every configured Gemini key tried in turn, starting from a rotating offset
    so load spreads across the pool instead of always hammering key #1,
  * bounded backoff on the transient failures worth retrying,
  * an OpenAI fallback when the whole Gemini pool is exhausted,
  * a hard exception when nothing succeeded, so callers fail loudly rather
    than silently degrading to a static layout.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger("homevision")

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Transient conditions worth spending another key/attempt on.  Anything else
# (a malformed schema, a prompt rejection) fails fast — retrying cannot help.
_RETRYABLE_PATTERNS = (
    "429", "too many requests", "quota", "rate limit", "resource exhausted",
    "500", "502", "503", "504", "internal error", "unavailable", "overloaded",
    "deadline", "timeout", "timed out", "connection", "temporarily",
)

# Key-level conditions: this key is unusable, move to the next one immediately.
_KEY_FATAL_PATTERNS = (
    "api key not valid", "api_key_invalid", "invalid api key", "permission denied",
    "unauthorized", "401", "403", "expired", "suspended", "billing",
)

_lock = threading.Lock()
_counter = itertools.count()
_disabled_keys: Dict[str, float] = {}  # key -> unix ts when it may be retried
_KEY_COOLDOWN_SECONDS = float(os.getenv("LLM_KEY_COOLDOWN_SECONDS", "300"))


def _split_keys(raw: str) -> List[str]:
    return [k.strip() for k in re.split(r"[,\s;]+", raw or "") if k.strip()]


def gemini_keys() -> List[str]:
    """Configured Gemini keys, de-duplicated, order preserved.

    ``GEMINI_API_KEYS`` holds the rotation pool; ``GEMINI_API_KEY`` /
    ``GOOGLE_API_KEY`` stay supported so existing deployments keep working.
    """
    raw = " ".join(filter(None, (
        os.getenv("GEMINI_API_KEYS", ""),
        os.getenv("GEMINI_API_KEY", ""),
        os.getenv("GOOGLE_API_KEY", ""),
    )))
    seen, keys = set(), []
    for key in _split_keys(raw):
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def openai_keys() -> List[str]:
    raw = " ".join(filter(None, (
        os.getenv("OPENAI_API_KEYS", ""),
        os.getenv("OPENAI_API_KEY", ""),
    )))
    seen, keys = set(), []
    for key in _split_keys(raw):
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def has_llm_credentials() -> bool:
    return bool(gemini_keys() or openai_keys())


def _mask(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "…"


def _classify(exc: BaseException) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if any(p in text for p in _KEY_FATAL_PATTERNS):
        return "key_fatal"
    if any(p in text for p in _RETRYABLE_PATTERNS):
        return "retryable"
    return "fatal"


def _cooldown(key: str) -> None:
    with _lock:
        _disabled_keys[key] = time.time() + _KEY_COOLDOWN_SECONDS
    logger.warning("[LLM POOL] Key %s parked for %.0fs", _mask(key), _KEY_COOLDOWN_SECONDS)


def _live_keys(keys: List[str]) -> List[str]:
    now = time.time()
    with _lock:
        live = [k for k in keys if _disabled_keys.get(k, 0.0) <= now]
    # Every key is cooling down: ignore the cooldown rather than give up, a
    # stale 429 must never be the reason a user gets no house at all.
    return live or keys


def _rotated(keys: List[str]) -> List[str]:
    if len(keys) <= 1:
        return list(keys)
    offset = next(_counter) % len(keys)
    return keys[offset:] + keys[:offset]


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json(text: str) -> Dict[str, Any]:
    cleaned = _strip_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Models occasionally wrap the object in prose; recover the outermost
        # JSON object rather than discarding a usable answer.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _call_gemini(
    key: str,
    model: str,
    contents: str,
    system_instruction: str,
    response_schema: Optional[Type],
    temperature: float,
    timeout_ms: int,
) -> Dict[str, Any]:
    from google import genai

    client = genai.Client(api_key=key, http_options=genai.types.HttpOptions(timeout=timeout_ms))
    config_kwargs: Dict[str, Any] = {
        "response_mime_type": "application/json",
        "temperature": temperature,
    }
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=genai.types.GenerateContentConfig(**config_kwargs),
    )
    return _parse_json(response.text)


def _call_openai(
    key: str,
    model: str,
    contents: str,
    system_instruction: str,
    response_schema: Optional[Type],
    temperature: float,
    timeout_s: float,
) -> Dict[str, Any]:
    import requests

    system = system_instruction or "You are a precise JSON generator."
    if response_schema is not None and hasattr(response_schema, "model_json_schema"):
        system += (
            "\n\nReturn ONLY a JSON object conforming to this JSON Schema:\n"
            + json.dumps(response_schema.model_json_schema())
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": contents},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_s,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI {resp.status_code}: {resp.text[:300]}")
    return _parse_json(resp.json()["choices"][0]["message"]["content"])


def generate_json(
    contents: str,
    system_instruction: str = "",
    response_schema: Optional[Type] = None,
    temperature: float = 0.2,
    model: Optional[str] = None,
    timeout_ms: int = 20000,
    attempts_per_key: int = 2,
    stage: str = "llm",
) -> Dict[str, Any]:
    """Return a parsed JSON object, rotating keys and providers until one works.

    Raises ``RuntimeError`` when every key and provider failed; callers must
    surface that instead of substituting a placeholder layout.
    """
    model = model or DEFAULT_GEMINI_MODEL
    errors: List[str] = []

    for key in _rotated(_live_keys(gemini_keys())):
        for attempt in range(max(1, attempts_per_key)):
            try:
                result = _call_gemini(
                    key, model, contents, system_instruction,
                    response_schema, temperature, timeout_ms,
                )
                if attempt or errors:
                    logger.info("[LLM POOL] %s recovered on Gemini key %s", stage, _mask(key))
                return result
            except Exception as exc:  # noqa: BLE001 - provider errors are opaque
                kind = _classify(exc)
                errors.append(f"gemini/{_mask(key)}: {exc}")
                logger.warning("[LLM POOL] %s Gemini key %s failed (%s): %s",
                               stage, _mask(key), kind, str(exc)[:200])
                if kind == "key_fatal":
                    _cooldown(key)
                    break
                if kind == "fatal":
                    break
                if attempt + 1 < attempts_per_key:
                    time.sleep(min(4.0, 0.6 * (2 ** attempt)) + random.uniform(0, 0.3))
                else:
                    _cooldown(key)

    for key in _rotated(_live_keys(openai_keys())):
        try:
            logger.info("[LLM POOL] %s falling back to OpenAI", stage)
            result = _call_openai(
                key, DEFAULT_OPENAI_MODEL, contents, system_instruction,
                response_schema, temperature, timeout_ms / 1000.0,
            )
            logger.info("[LLM POOL] %s served by OpenAI fallback", stage)
            return result
        except Exception as exc:  # noqa: BLE001
            errors.append(f"openai/{_mask(key)}: {exc}")
            logger.warning("[LLM POOL] %s OpenAI key %s failed: %s", stage, _mask(key), str(exc)[:200])
            if _classify(exc) == "key_fatal":
                _cooldown(key)

    detail = " | ".join(errors[-4:]) or "no API keys configured"
    raise RuntimeError(f"All LLM providers failed for {stage}: {detail}")
