"""Thin LLM client for RAG preprocessing and parcel synthesis.

Primary provider is Gemini by default. When both providers are configured and
LLM_FALLBACK_ENABLED is true, rate-limit / quota / overload errors automatically
retry on the other provider via an OpenAI-compatible chat.completions interface.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

from services.llm_concurrency import llm_slot
from services.rag_settings import rag_settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

T = TypeVar("T", bound=BaseModel)

_openai_client = None
_gemini_client = None


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt file missing: {path}")
    return path.read_text(encoding="utf-8")


def get_openai_client():
    """Singleton OpenAI client with configured timeout and retries."""
    global _openai_client
    if not rag_settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured in backend/.env")
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI(
            api_key=rag_settings.openai_api_key,
            timeout=rag_settings.openai_timeout_seconds,
            max_retries=rag_settings.openai_max_retries,
        )
    return _openai_client


def get_gemini_client():
    """Singleton Gemini client via Google's OpenAI-compatible endpoint."""
    global _gemini_client
    if not (rag_settings.gemini_api_key or "").strip():
        raise RuntimeError("GEMINI_API_KEY is not configured in backend/.env")
    if _gemini_client is None:
        from openai import OpenAI

        _gemini_client = OpenAI(
            api_key=rag_settings.gemini_api_key.strip(),
            base_url=(rag_settings.gemini_base_url or "").strip()
            or "https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=rag_settings.openai_timeout_seconds,
            max_retries=rag_settings.openai_max_retries,
        )
    return _gemini_client


def gemini_configured() -> bool:
    return bool((rag_settings.gemini_api_key or "").strip())


def openai_configured() -> bool:
    return bool((rag_settings.openai_api_key or "").strip())


def ping_openai() -> None:
    """Lightweight readiness check — fetch first model page."""
    client = get_openai_client()
    for _ in client.models.list():
        return


def ping_gemini() -> None:
    """Lightweight readiness check for Gemini (OpenAI-compatible models list)."""
    client = get_gemini_client()
    for _ in client.models.list():
        return


def _primary_provider() -> str:
    raw = (rag_settings.llm_primary_provider or "gemini").strip().lower()
    return raw if raw in ("openai", "gemini") else "gemini"


def _fallback_enabled() -> bool:
    return bool(rag_settings.llm_fallback_enabled) and gemini_configured() and openai_configured()


def _is_failover_error(exc: BaseException) -> bool:
    """True for rate limits, quota exhaustion, and temporary provider overload."""
    try:
        from openai import APIStatusError, RateLimitError

        if isinstance(exc, RateLimitError):
            return True
        if isinstance(exc, APIStatusError) and getattr(exc, "status_code", None) in (429, 503):
            return True
    except ImportError:
        pass

    msg = str(exc).lower()
    markers = (
        "rate limit",
        "rate_limit",
        "ratelimit",
        "insufficient_quota",
        "credit_balance_exhausted",
        "no credits remaining",
        "quota exceeded",
        "billing",
        "too many requests",
        "overloaded",
        "server_error",
        "temporarily unavailable",
        "service unavailable",
        "429",
    )
    return any(m in msg for m in markers)


def _is_router_model(model: str) -> bool:
    m = model.lower()
    return (
        m == rag_settings.rag_router_model.lower()
        or m == rag_settings.gemini_router_model.lower()
        or "mini" in m
        or "flash-lite" in m
        or m.endswith("-lite")
    )


def _map_model_for_provider(model: Optional[str], provider: str, *, json_mode: bool) -> str:
    """Map model names between OpenAI and Gemini when switching providers."""
    requested = (model or "").strip()
    use_router = _is_router_model(requested) if requested else json_mode

    if provider == "gemini":
        if requested.lower().startswith("gemini"):
            return requested
        return (
            rag_settings.gemini_router_model
            if use_router
            else rag_settings.gemini_synthesis_model
        )

    # provider == openai
    if not requested or requested.lower().startswith("gemini"):
        return (
            rag_settings.rag_router_model
            if use_router
            else rag_settings.rag_synthesis_model
        )
    return requested


def _client_for(provider: str):
    if provider == "gemini":
        return get_gemini_client()
    return get_openai_client()


def _provider_order() -> List[str]:
    primary = _primary_provider()
    if primary == "gemini":
        if not gemini_configured():
            if openai_configured():
                return ["openai"]
            raise RuntimeError("GEMINI_API_KEY is not configured (LLM_PRIMARY_PROVIDER=gemini)")
        order = ["gemini"]
        if _fallback_enabled() or (openai_configured() and rag_settings.llm_fallback_enabled):
            if openai_configured():
                order.append("openai")
        return order

    if not openai_configured():
        if gemini_configured():
            logger.warning("OPENAI_API_KEY missing — using Gemini as sole LLM provider")
            return ["gemini"]
        raise RuntimeError("OPENAI_API_KEY is not configured in backend/.env")

    order = ["openai"]
    if _fallback_enabled():
        order.append("gemini")
    return order


def _chat_completion(
    *,
    system_prompt: str,
    user_content: str,
    model: Optional[str],
    temperature: float,
    json_mode: bool,
) -> str:
    """Call primary LLM; on rate-limit/quota/overload, retry on the backup provider."""
    last_error: Optional[BaseException] = None
    providers = _provider_order()

    for idx, provider in enumerate(providers):
        model_name = _map_model_for_provider(model, provider, json_mode=json_mode)
        try:
            client = _client_for(provider)
            kwargs: Dict[str, Any] = {
                "model": model_name,
                "temperature": temperature,
                # Gemini free/default caps often truncate JSON mid-object without this.
                "max_tokens": 8192,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            response = client.chat.completions.create(**kwargs)
            content = (response.choices[0].message.content or "").strip()
            finish = getattr(response.choices[0], "finish_reason", None)
            if finish == "length":
                logger.warning(
                    "LLM output truncated (finish_reason=length) provider=%s model=%s chars=%s",
                    provider,
                    model_name,
                    len(content),
                )
            if idx > 0:
                logger.warning(
                    "LLM failover succeeded via %s model=%s (after %s failed)",
                    provider,
                    model_name,
                    providers[0],
                )
            else:
                logger.debug("LLM call ok provider=%s model=%s", provider, model_name)
            return content
        except Exception as exc:
            last_error = exc
            has_next = idx + 1 < len(providers)
            if has_next and _is_failover_error(exc):
                logger.warning(
                    "LLM provider %s failed (%s); falling back to %s",
                    provider,
                    exc,
                    providers[idx + 1],
                )
                continue
            if has_next:
                logger.warning(
                    "LLM provider %s failed with non-failover error; not retrying backup: %s",
                    provider,
                    exc,
                )
            raise

    assert last_error is not None
    raise last_error


def _strip_json_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _close_truncated_json(raw: str) -> str:
    """Best-effort close for outputs cut off mid-object (common when max tokens hit)."""
    s = raw.rstrip()
    if not s.startswith("{"):
        return s

    in_string = False
    escape = False
    stack: List[str] = []
    for ch in s:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    if in_string:
        s += '"'
    # Drop a dangling incomplete key/value tail after last safe delimiter.
    # e.g. truncated `"conditions": [` mid-key → already handled by closing quotes/brackets.
    while stack:
        opener = stack.pop()
        s += "}" if opener == "{" else "]"
    return s


def _loads_llm_json(raw: str) -> Dict[str, Any]:
    cleaned = _strip_json_fences(raw or "")
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
        raise json.JSONDecodeError("expected object", cleaned, 0)
    except json.JSONDecodeError:
        repaired = _close_truncated_json(cleaned)
        data = json.loads(repaired)
        if not isinstance(data, dict):
            raise json.JSONDecodeError("expected object", repaired, 0)
        logger.warning("Repaired truncated LLM JSON (added closing brackets/braces)")
        return data


def chat_json(
    *,
    system_prompt: str,
    user_content: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
) -> Dict[str, Any]:
    """Call chat completions and parse JSON object response (OpenAI, with Gemini fallback)."""
    last_raw = ""
    last_exc: Optional[BaseException] = None
    with llm_slot():
        for attempt in range(2):
            raw = _chat_completion(
                system_prompt=system_prompt,
                user_content=user_content,
                model=model or rag_settings.rag_router_model,
                temperature=temperature,
                json_mode=True,
            ) or "{}"
            last_raw = raw
            try:
                return _loads_llm_json(raw)
            except json.JSONDecodeError as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning(
                        "LLM JSON parse failed (attempt 1); retrying once. preview=%s",
                        raw[:240],
                    )
                    continue
    logger.error("LLM returned non-JSON: %s", last_raw[:800])
    raise RuntimeError("LLM returned invalid JSON") from last_exc


def chat_text(
    *,
    system_prompt: str,
    user_content: str,
    model: Optional[str] = None,
    temperature: float = 0.1,
) -> str:
    with llm_slot():
        return _chat_completion(
            system_prompt=system_prompt,
            user_content=user_content,
            model=model or rag_settings.rag_synthesis_model,
            temperature=temperature,
            json_mode=False,
        )


def render_prompt(template_name: str, **kwargs: str) -> str:
    text = _load_prompt(template_name)
    for key, value in kwargs.items():
        text = text.replace("{{" + key + "}}", value)
    return text
