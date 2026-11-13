import json

import httpx
import pytest

from engenia.harness.limits import Limits
from engenia.harness.llm import (
    FakeLLM,
    FakeLLMExhausted,
    LLMError,
    chat,
    message_of,
    text_response,
    tool_call_response,
)

OK_BODY = text_response("hello")


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setenv("ENGENIA_LLM_BASE_URL", "https://llm.test/v1/")
    monkeypatch.setenv("ENGENIA_LLM_API_KEY", "sk-secret")


def test_chat_posts_openai_request_and_returns_body(endpoint):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    tools = [{"type": "function", "function": {"name": "t"}}]
    messages = [{"role": "user", "content": "hi"}]
    response = chat("m1", messages, tools, 0.2, client=client_for(handler))

    assert message_of(response)["content"] == "hello"
    (request,) = seen
    assert str(request.url) == "https://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-secret"
    body = json.loads(request.content)
    assert body == {"model": "m1", "messages": messages, "temperature": 0.2, "tools": tools}


def test_chat_omits_tools_when_none(endpoint):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=OK_BODY)

    chat("m", [], client=client_for(handler))
    assert "tools" not in seen[0]
    assert seen[0]["temperature"] == 0


def test_chat_retries_429_then_succeeds(endpoint):
    statuses = iter([429, 503, 200])
    sleeps = []

    def handler(request):
        status = next(statuses)
        return httpx.Response(status, json=OK_BODY if status == 200 else {"error": "busy"})

    response = chat("m", [], client=client_for(handler), sleep=sleeps.append)
    assert message_of(response)["content"] == "hello"
    assert sleeps == [1.0, 2.0]


def test_chat_gives_up_after_four_tries(endpoint):
    calls = []
    sleeps = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, text="boom")

    with pytest.raises(LLMError, match="after 4 tries"):
        chat("m", [], client=client_for(handler), sleep=sleeps.append)
    assert len(calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]  # no wait after the last try


def test_chat_does_not_retry_other_4xx(endpoint):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text="bad request")

    with pytest.raises(LLMError, match="HTTP 400"):
        chat("m", [], client=client_for(handler), sleep=lambda s: None)
    assert len(calls) == 1


def test_chat_honours_retry_after(endpoint):
    responses = iter(
        [httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json=OK_BODY)]
    )
    sleeps = []
    chat("m", [], client=client_for(lambda r: next(responses)), sleep=sleeps.append)
    assert sleeps == [7.0]


def test_chat_retries_transport_errors(endpoint):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=OK_BODY)

    chat("m", [], client=client_for(handler), sleep=lambda s: None)
    assert len(attempts) == 2


def test_chat_requires_base_url():
    with pytest.raises(LLMError, match="ENGENIA_LLM_BASE_URL"):
        chat("m", [])


def test_chat_rejects_malformed_body(endpoint):
    with pytest.raises(LLMError, match="choices"):
        chat("m", [], client=client_for(lambda r: httpx.Response(200, json={"nope": 1})))


def test_error_message_does_not_leak_api_key(endpoint):
    with pytest.raises(LLMError) as info:
        chat("m", [], client=client_for(lambda r: httpx.Response(401, text="no")))
    assert "sk-secret" not in str(info.value)


# --- FakeLLM ----------------------------------------------------------------------------------


def test_fake_llm_plays_script_and_records_calls():
    fake = FakeLLM([tool_call_response(("run_build", {})), text_response("done")])
    messages = [{"role": "user", "content": "go"}]
    first = fake("m", messages, None, 0)
    messages.append({"role": "assistant", "content": "later change"})
    second = fake("m", messages)

    assert message_of(first)["tool_calls"][0]["function"]["name"] == "run_build"
    assert message_of(second)["content"] == "done"
    assert len(fake.calls[0].messages) == 1  # recorded as it was at call time
    assert fake.remaining == 0
    with pytest.raises(FakeLLMExhausted):
        fake("m", messages)


def test_fake_llm_raises_scripted_exception_and_calls_callables():
    fake = FakeLLM([LLMError("down"), lambda messages: text_response(str(len(messages)))])
    with pytest.raises(LLMError, match="down"):
        fake("m", [])
    assert message_of(fake("m", [{"role": "user", "content": "x"}]))["content"] == "1"


# --- Limits -----------------------------------------------------------------------------------


def test_limits_defaults():
    limits = Limits()
    assert (limits.max_attempts, limits.max_tool_calls_per_attempt) == (3, 40)
    assert limits.max_wall_seconds_per_attempt == 600


def test_limits_from_env(monkeypatch):
    assert Limits.from_env().max_attempts == 3
    monkeypatch.setenv("ENGENIA_MAX_ATTEMPTS", "5")
    limits = Limits.from_env()
    assert limits.max_attempts == 5
    assert limits.max_tool_calls_per_attempt == 40


@pytest.mark.parametrize("value", ["abc", "0", "-2", "1.5"])
def test_limits_reject_bad_env(monkeypatch, value):
    monkeypatch.setenv("ENGENIA_MAX_ATTEMPTS", value)
    with pytest.raises(ValueError, match="ENGENIA_MAX_ATTEMPTS"):
        Limits.from_env()


def test_limits_reject_non_positive_values():
    with pytest.raises(ValueError):
        Limits(max_tool_calls_per_attempt=0)
    with pytest.raises(ValueError):
        Limits(max_wall_seconds_per_attempt=0)
