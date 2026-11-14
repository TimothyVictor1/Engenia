"""The agent loop: model call, run the tool calls, feed results back, until a final message.

One call of `run_agent` is one attempt. The loop stops when the model answers without tool calls
or when a per-attempt limit is hit. It never looks at what the model said to decide whether the
design is good; that is the job of the verifiers (see design_loop.py).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from engenia.core.events import agent_context
from engenia.harness.limits import Limits
from engenia.harness.llm import ChatFn, LLMError, chat, message_of
from engenia.harness.tools import Toolbox, ToolError

AgentStatus = Literal["done", "tool_limit", "time_limit", "error"]
"""done: the model gave a final message. tool_limit / time_limit: a per-attempt limit stopped it.
error: the model endpoint failed after retries."""

TRANSCRIPT_FORMAT = 1


@dataclass
class AgentResult:
    status: AgentStatus
    attempts: int  # the attempt number this run was, as given by the caller (1 for a lone run)
    transcript_path: Path
    messages: list[dict[str, Any]] = field(default_factory=list, repr=False)
    tool_calls: int = 0
    error: str | None = None


def transcript_file(transcript_dir: Path, attempt: int) -> Path:
    return transcript_dir / f"attempt-{attempt:02d}.json"


def _write_transcript(
    path: Path,
    *,
    attempt: int,
    status: str,
    error: str | None,
    model: str,
    temperature: float,
    limits: Limits,
    tool_names: list[str],
    tool_calls: int,
    start_index: int,
    messages: list[dict[str, Any]],
) -> None:
    # No timestamps or durations: the file should depend only on what was said and done.
    record = {
        "format": TRANSCRIPT_FORMAT,
        "attempt": attempt,
        "status": status,
        "error": error,
        "model": model,
        "temperature": temperature,
        "limits": asdict(limits),
        "tools": tool_names,
        "tool_calls": tool_calls,
        "attempt_start_index": start_index,
        "messages": messages,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _execute_tool_call(tools: Toolbox, call: dict[str, Any]) -> str:
    """Run one requested tool call and return the tool-result text. Never raises."""
    function = call.get("function")
    if not isinstance(function, dict):
        return json.dumps({"error": "malformed tool call: no function"})
    name = function.get("name")
    raw_args = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"tool arguments are not valid JSON: {exc}"})
    if not isinstance(arguments, dict):
        return json.dumps({"error": "tool arguments must be a JSON object"})
    try:
        return json.dumps(tools.call(str(name), arguments))
    except ToolError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:  # a tool bug must not kill the session; the model sees the failure
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def _call_summary(call: dict[str, Any]) -> dict[str, Any]:
    """Name and arguments of a tool call for the log. File contents live in the code event."""
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = function.get("name")
    raw = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {"name": name, "arguments": {"raw": str(raw)[:2000]}}
    if name == "write_file" and isinstance(arguments, dict):
        arguments = {"path": arguments.get("path"), "bytes": len(str(arguments.get("content", "")))}
    return {"name": name, "arguments": arguments}


def run_agent(
    system_prompt: str,
    task: str,
    tools: Toolbox,
    model: str,
    limits: Limits,
    *,
    transcript_dir: Path,
    attempt: int = 1,
    history: list[dict[str, Any]] | None = None,
    chat_fn: ChatFn = chat,
    temperature: float = 0,
    clock: Callable[[], float] = time.monotonic,
    agent: str | None = None,
) -> AgentResult:
    """Run one attempt and save its transcript to transcript_dir/attempt-NN.json.

    With `history` (the `messages` of an earlier AgentResult) the conversation continues: `task`
    is appended to it as the next user message and the system prompt is not repeated.
    """
    messages: list[dict[str, Any]] = list(history) if history else [
        {"role": "system", "content": system_prompt}
    ]
    start_index = len(messages)
    messages.append({"role": "user", "content": task})

    started = clock()
    tool_calls_used = 0
    status: str = "crashed"  # only visible in the transcript if an unexpected exception escapes
    error: str | None = None
    schemas = tools.schemas()
    sink = tools.events
    if sink is not None:
        sink.set_attempt(attempt)
    path = transcript_file(transcript_dir, attempt)

    with agent_context(agent or (sink.agent if sink is not None else "unknown"), model):
        try:
            while True:
                if clock() - started >= limits.max_wall_seconds_per_attempt:
                    status = "time_limit"
                    break
                try:
                    reply = message_of(chat_fn(model, messages, schemas, temperature))
                except LLMError as exc:
                    status, error = "error", str(exc)
                    break
                requested = reply.get("tool_calls") or []
                assistant: dict[str, Any] = {"role": "assistant", "content": reply.get("content")}
                if requested:
                    assistant["tool_calls"] = requested
                messages.append(assistant)
                if sink is not None:
                    # One event per model turn, tool-call turns included, so the log can count
                    # model calls per agent.
                    sink.emit("message", {
                        "role": "assistant", "text": reply.get("content") or "",
                        "tool_calls": len(requested),
                    })
                if not requested:
                    status = "done"
                    break

                stopped: str | None = None
                for index, call in enumerate(requested):
                    call_id = call.get("id") or f"call_missing_{index}"
                    if stopped is None and tool_calls_used >= limits.max_tool_calls_per_attempt:
                        stopped = "tool_limit"
                    elif (stopped is None
                          and clock() - started >= limits.max_wall_seconds_per_attempt):
                        stopped = "time_limit"
                    if stopped is not None:
                        # Every requested call needs a result or the conversation cannot continue.
                        reason = stopped.replace("_", " ")
                        content = json.dumps({"error": f"not executed: {reason}"})
                    else:
                        tool_calls_used += 1
                        if sink is not None:
                            sink.emit("tool_call", {"id": call_id, **_call_summary(call)})
                        content = _execute_tool_call(tools, call)
                        if sink is not None:
                            sink.emit("tool_result", {"id": call_id, "content": content[:4000]})
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
                if stopped is not None:
                    status = stopped
                    break
        finally:
            _write_transcript(
                path,
                attempt=attempt,
                status=status,
                error=error,
                model=model,
                temperature=temperature,
                limits=limits,
                tool_names=[s["function"]["name"] for s in schemas],
                tool_calls=tool_calls_used,
                start_index=start_index,
                messages=messages,
            )

    return AgentResult(
        status=status,  # type: ignore[arg-type]
        attempts=attempt,
        transcript_path=path,
        messages=messages,
        tool_calls=tool_calls_used,
        error=error,
    )
