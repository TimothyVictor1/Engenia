"""A small OpenAI-compatible chat client, plus a scripted FakeLLM for tests.

Configuration comes from the environment at call time:
  ENGENIA_LLM_BASE_URL  e.g. https://api.example.com/v1  (required)
  ENGENIA_LLM_API_KEY   sent as a bearer token when set

Model-family guard: the design model (ENGENIA_DESIGN_MODEL) and the verification model
(ENGENIA_VERIFY_MODEL) must not be the same model or share a family, so that one model never
checks its own work. See `check_model_families`.
"""

from __future__ import annotations

import copy
import itertools
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

MAX_TRIES = 4
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 120.0


class LLMError(RuntimeError):
    """The model endpoint could not be used. The message never contains the API key."""


ALLOW_SAME_FAMILY_ENV = "ENGENIA_ALLOW_SAME_FAMILY"


class ModelFamilyError(RuntimeError):
    """The design and verification models are the same, or share a family."""


def model_family(model_id: str) -> str:
    """The part of a Nebius-style ID before "/" ("Qwen/Qwen3-235B" -> "qwen"), lower case.

    An ID with no "/" is its own family, so two equal IDs still collide.
    """
    model_id = model_id.strip().lower()
    return model_id.split("/", 1)[0] if "/" in model_id else model_id


def check_model_families(env: Mapping[str, str] | None = None) -> str | None:
    """Compare ENGENIA_DESIGN_MODEL and ENGENIA_VERIFY_MODEL.

    Returns None when they differ in family, or when either is unset (nothing to compare).
    When they are equal or share a family: raises ModelFamilyError, unless
    ENGENIA_ALLOW_SAME_FAMILY=1, in which case the override is returned as a sentence for
    build/final/audit.md.
    """
    source = os.environ if env is None else env
    design = source.get("ENGENIA_DESIGN_MODEL", "").strip()
    verify = source.get("ENGENIA_VERIFY_MODEL", "").strip()
    if not design or not verify:
        return None
    if model_family(design) != model_family(verify):
        return None
    if source.get(ALLOW_SAME_FAMILY_ENV, "").strip() == "1":
        return (
            f"{ALLOW_SAME_FAMILY_ENV}=1 overrode the model-family guard: design model "
            f"{design!r} and verification model {verify!r} share the family "
            f"{model_family(design)!r}."
        )
    raise ModelFamilyError(
        f"design model {design!r} (ENGENIA_DESIGN_MODEL) and verification model {verify!r} "
        f"(ENGENIA_VERIFY_MODEL) are the same or share the family {model_family(design)!r}. "
        "A model must not check its own family's work. Choose models from different families, "
        f"or set {ALLOW_SAME_FAMILY_ENV}=1 to override (the override is recorded in "
        "build/final/audit.md)."
    )


class ChatFn(Protocol):
    """The call shape shared by `chat` and `FakeLLM`."""

    def __call__(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0,
    ) -> dict[str, Any]: ...


def _retry_delay(response: httpx.Response | None, try_number: int) -> float:
    """Seconds to wait before the next try. Honours a numeric Retry-After header."""
    if response is not None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(max(float(header), 0.0), BACKOFF_CAP_SECONDS)
            except ValueError:
                pass
    return min(BACKOFF_BASE_SECONDS * 2 ** (try_number - 1), BACKOFF_CAP_SECONDS)


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"LLM returned a body that is not JSON: {exc}") from None
    message_of(data)  # validates the shape
    return data


def message_of(response: dict[str, Any]) -> dict[str, Any]:
    """The assistant message of a chat completion response. Raises LLMError if malformed."""
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("LLM response has no choices[0].message") from None
    if not isinstance(message, dict):
        raise LLMError("LLM response message is not an object")
    return message


def chat(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """POST one chat completion and return the parsed response body.

    Retries on 429, on 5xx and on transport errors (timeouts, dropped connections), with
    exponential backoff, at most MAX_TRIES tries in all. Any other 4xx raises at once.
    `client` and `sleep` exist so tests can run without a network or real waiting.
    """
    base_url = os.environ.get("ENGENIA_LLM_BASE_URL", "").strip()
    if not base_url:
        raise LLMError("ENGENIA_LLM_BASE_URL is not set")
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("ENGENIA_LLM_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools

    owns_client = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        last_failure = "no attempt made"
        for try_number in range(1, MAX_TRIES + 1):
            response: httpx.Response | None = None
            try:
                response = http.post(url, json=payload, headers=headers)
            except httpx.TransportError as exc:
                last_failure = f"transport error: {type(exc).__name__}"
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last_failure = f"HTTP {response.status_code}: {response.text[:200]}"
                elif not response.is_success:
                    raise LLMError(f"LLM request rejected: HTTP {response.status_code}: "
                                   f"{response.text[:500]}")
                else:
                    return _parse_response(response)
            if try_number < MAX_TRIES:
                sleep(_retry_delay(response, try_number))
        raise LLMError(f"LLM request failed after {MAX_TRIES} tries: {last_failure}")
    finally:
        if owns_client:
            http.close()


# --- FakeLLM ----------------------------------------------------------------------------------

_call_ids = itertools.count(1)


def text_response(content: str) -> dict[str, Any]:
    """A scripted final answer with no tool calls."""
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ]
    }


def tool_call_response(
    *calls: tuple[str, dict[str, Any]], content: str | None = None
) -> dict[str, Any]:
    """A scripted assistant turn requesting one or more tool calls: (name, arguments) pairs."""
    tool_calls = [
        {
            "id": f"call_{next(_call_ids)}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }
        for name, arguments in calls
    ]
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                "finish_reason": "tool_calls",
            }
        ]
    }


class FakeLLMExhausted(AssertionError):
    """The code under test made more model calls than the script has responses."""


@dataclass
class FakeCall:
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    temperature: float


class FakeLLM:
    """Scripted stand-in for `chat`. Each model call consumes the next script entry.

    An entry is a response dict (see text_response and tool_call_response), an Exception
    instance to raise, or a callable taking the message list and returning a response dict.
    Every call is recorded in `calls` with a deep copy of the messages it received.
    """

    def __init__(self, script: Sequence[dict[str, Any] | Exception | Callable[..., Any]]):
        self._script = list(script)
        self.calls: list[FakeCall] = []

    def __call__(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0,
    ) -> dict[str, Any]:
        self.calls.append(FakeCall(model, copy.deepcopy(messages), tools, temperature))
        if not self._script:
            raise FakeLLMExhausted(f"FakeLLM script exhausted at call {len(self.calls)}")
        entry = self._script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        if callable(entry):
            return entry(copy.deepcopy(messages))
        return copy.deepcopy(entry)

    @property
    def remaining(self) -> int:
        return len(self._script)
