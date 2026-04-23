"""OpenAI-compatible shim over the official google-genai SDK.

Existing call sites use `client.chat.completions.create(...)`; this module lets them
keep that shape while forwarding to the Gemini API (`client.models.generate_content`).

Model-name convention recognized by the shim:
  - `*-search` suffix   → enables the `google_search` tool
  - `*-nothinking` suffix → plain generate_content, no tools
  - If `extra_body={"web_search_options": {...}}` is passed, search is forced on
    regardless of the model suffix.

The suffix is stripped before the request is sent, so the underlying model id
becomes e.g. `gemini-3-flash-preview`.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

from google import genai
from google.genai import types


_SEARCH_SUFFIXES = ("-search",)
_PLAIN_SUFFIXES = ("-nothinking",)


def _strip_suffix(model: str) -> str:
    if not model:
        return model
    for suffix in _SEARCH_SUFFIXES + _PLAIN_SUFFIXES:
        if model.endswith(suffix):
            return model[: -len(suffix)]
    return model


def _implies_search(model: str) -> bool:
    return any(model.endswith(s) for s in _SEARCH_SUFFIXES)


def _split_system(messages: List[Dict[str, Any]]) -> tuple[Optional[str], List[Dict[str, Any]]]:
    sys_parts: List[str] = []
    rest: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            c = msg.get("content", "")
            if c:
                sys_parts.append(c)
        else:
            rest.append(msg)
    return ("\n\n".join(sys_parts) or None), rest


def _to_contents(messages: List[Dict[str, Any]]):
    """OpenAI messages → Gemini `contents` (string or list of dicts)."""
    if not messages:
        return ""
    if len(messages) == 1 and messages[0].get("role") == "user":
        return messages[0].get("content", "") or ""
    out = []
    for msg in messages:
        role = msg.get("role", "user")
        gemini_role = "model" if role == "assistant" else "user"
        out.append({"role": gemini_role, "parts": [{"text": msg.get("content", "") or ""}]})
    return out


def _build_config(
    *,
    system: Optional[str],
    max_tokens: Optional[int],
    response_format: Optional[Dict[str, Any]],
    enable_search: bool,
    temperature: Optional[float],
) -> Optional[types.GenerateContentConfig]:
    kwargs: Dict[str, Any] = {}
    if system:
        kwargs["system_instruction"] = system
    if max_tokens:
        kwargs["max_output_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    if response_format and response_format.get("type") == "json_object" and not enable_search:
        kwargs["response_mime_type"] = "application/json"
    if enable_search:
        kwargs["tools"] = [{"google_search": {}}]
    return types.GenerateContentConfig(**kwargs) if kwargs else None


def _extract_text(resp) -> str:
    text = getattr(resp, "text", None)
    if text:
        return text
    try:
        parts = resp.candidates[0].content.parts or []
        return "".join(getattr(p, "text", "") or "" for p in parts)
    except Exception:
        return ""


# ─────────────────────────── response shim ───────────────────────────

class _Delta:
    __slots__ = ("content",)
    def __init__(self, content: str): self.content = content


class _Message:
    __slots__ = ("content",)
    def __init__(self, content: str): self.content = content


class _Choice:
    __slots__ = ("message", "delta")
    def __init__(self, content: str):
        self.message = _Message(content)
        self.delta = _Delta(content)


class _Response:
    __slots__ = ("choices",)
    def __init__(self, content: str): self.choices = [_Choice(content)]


# ─────────────────────────── sync client ───────────────────────────

class _Completions:
    def __init__(self, client: "GeminiClient"): self._c = client

    def create(
        self, *, model: str, messages: List[Dict[str, Any]],
        stream: bool = False, temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        **_: Any,
    ):
        enable_search = bool(extra_body and "web_search_options" in extra_body) or _implies_search(model)
        actual_model = _strip_suffix(model)
        system, rest = _split_system(messages)
        contents = _to_contents(rest)
        config = _build_config(
            system=system, max_tokens=max_tokens, response_format=response_format,
            enable_search=enable_search, temperature=temperature,
        )
        if stream:
            return self._stream(actual_model, contents, config)
        resp = self._c._genai.models.generate_content(
            model=actual_model, contents=contents, config=config,
        )
        return _Response(_extract_text(resp))

    def _stream(self, model, contents, config) -> Iterator[_Response]:
        for chunk in self._c._genai.models.generate_content_stream(
            model=model, contents=contents, config=config,
        ):
            text = _extract_text(chunk)
            if text:
                yield _Response(text)


class _Chat:
    def __init__(self, client: "GeminiClient"): self.completions = _Completions(client)


class GeminiClient:
    """Drop-in replacement for `openai.OpenAI` for this project's call patterns."""
    def __init__(self, api_key: str, base_url: Optional[str] = None,
                 timeout: Optional[float] = None, **_: Any):
        self._genai = genai.Client(api_key=api_key)
        self.chat = _Chat(self)


# ─────────────────────────── async client ───────────────────────────

class _AsyncCompletions:
    def __init__(self, client: "AsyncGeminiClient"): self._c = client

    async def create(
        self, *, model: str, messages: List[Dict[str, Any]],
        stream: bool = False, temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        **_: Any,
    ):
        enable_search = bool(extra_body and "web_search_options" in extra_body) or _implies_search(model)
        actual_model = _strip_suffix(model)
        system, rest = _split_system(messages)
        contents = _to_contents(rest)
        config = _build_config(
            system=system, max_tokens=max_tokens, response_format=response_format,
            enable_search=enable_search, temperature=temperature,
        )
        if stream:
            return self._stream(actual_model, contents, config)
        resp = await self._c._genai.aio.models.generate_content(
            model=actual_model, contents=contents, config=config,
        )
        return _Response(_extract_text(resp))

    async def _stream(self, model, contents, config) -> AsyncIterator[_Response]:
        async for chunk in self._c._genai.aio.models.generate_content_stream(
            model=model, contents=contents, config=config,
        ):
            text = _extract_text(chunk)
            if text:
                yield _Response(text)


class _AsyncChat:
    def __init__(self, client: "AsyncGeminiClient"): self.completions = _AsyncCompletions(client)


class AsyncGeminiClient:
    """Drop-in replacement for `openai.AsyncOpenAI` for this project's call patterns."""
    def __init__(self, api_key: str, base_url: Optional[str] = None,
                 timeout: Optional[float] = None,
                 http_client: Any = None, max_retries: Any = None, **_: Any):
        self._genai = genai.Client(api_key=api_key)
        self.chat = _AsyncChat(self)
