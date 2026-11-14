"""Environment doctor for the engenia CLI: `engenia doctor`.

One read-only sweep of what a design run depends on, printed as a fixed-width table:

- Model configuration: ENGENIA_LLM_BASE_URL, ENGENIA_LLM_API_KEY, ENGENIA_DESIGN_MODEL and
  ENGENIA_VERIFY_MODEL. Values are read from the process environment first and from .env at the
  repository root second (PROJECT.md section 4). The pipeline itself reads the process
  environment only, so a value that exists only in .env is warned about, not counted as set.
- Model-family guard: the design and verification models must be from different families
  (engenia/harness/llm.py), so that one model never checks its own family's work.
- One tiny tool-calling request to each configured model, through the same `chat` client and
  endpoint the agents use, so a pass means the exact request shape a real design run makes.
- Local tools: uv, pio, the gmsh python module (which needs libGLU) and the capytaine module.

Exit code is 1 when any row fails. Warnings never change the exit code: with no usable API key
and endpoint the doctor reports "deterministic fallback mode" as a warning, because every design
agent then falls back to its deterministic builders. The API key is never printed: rows only say
"set", and every error detail is scrubbed against the known key values before display.
"""

from __future__ import annotations

import ctypes.util
import importlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal

import httpx

from engenia.harness.llm import (
    ALLOW_SAME_FAMILY_ENV,
    DESIGN_AGENTS,
    agent_model,
    chat,
    message_of,
    model_family,
)

#: The model-configuration variables checked from the process environment and from .env.
CONFIG_VARS = (
    "ENGENIA_LLM_BASE_URL",
    "ENGENIA_LLM_API_KEY",
    "ENGENIA_DESIGN_MODEL",
    "ENGENIA_VERIFY_MODEL",
    *(f"ENGENIA_MODEL_{agent.upper()}" for agent in DESIGN_AGENTS),
)

MIN_REDACT_CHARS = 8     # shorter values are never treated as secrets when scrubbing
MAX_DETAIL_CHARS = 200   # one table row stays one line
COMMAND_TIMEOUT_SECONDS = 60.0

RowResult = Literal["pass", "warn", "fail", "skip"]
CommandRunner = Callable[[list[str]], tuple[bool, str]]
ModuleImporter = Callable[[str], ModuleType]

PROBE_MESSAGES = [
    {"role": "system", "content": "You are a connectivity probe for an engineering pipeline."},
    {"role": "user", "content": "Call the doctor_ping tool once to confirm tool calling works."},
]

PROBE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "doctor_ping",
            "description": "Connectivity probe: confirm that this model can call tools.",
            "parameters": {
                "type": "object",
                "properties": {"note": {"type": "string", "description": "Any short note."}},
                "required": [],
            },
        },
    }
]


@dataclass(frozen=True)
class Row:
    """One line of the doctor table."""

    check: str
    result: RowResult
    detail: str


@dataclass(frozen=True)
class DoctorResult:
    """The rows, the rendered table and the process exit code of one doctor run."""

    rows: tuple[Row, ...]
    text: str
    exit_code: int


def read_env_file(root: Path) -> dict[str, str] | None:
    """Parse .env at the repository root; None when there is no file.

    Plain KEY=VALUE lines with an optional leading "export ", "#" comments, blank lines and
    quoted values are handled. Values are never echoed: callers only report whether a variable
    is set and where it was found.
    """
    path = root / ".env"
    if not path.is_file():
        return None
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _clean(text: object, secrets: tuple[str, ...]) -> str:
    """One-line detail: collapse whitespace, scrub known secrets, truncate."""
    line = " ".join(str(text).split())
    for secret in secrets:
        line = line.replace(secret, "***")
    if len(line) > MAX_DETAIL_CHARS:
        line = line[: MAX_DETAIL_CHARS - 3] + "..."
    return line


def _lookup(name: str, env_file: dict[str, str] | None) -> tuple[str, str]:
    """(value, source) for one variable: the process environment first, then .env."""
    value = os.environ.get(name, "").strip()
    if value:
        return value, "process environment"
    file_value = (env_file or {}).get(name, "").strip()
    if file_value:
        return file_value, ".env"
    return "", ""


def _valid_url(value: str) -> bool:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        return False
    return url.scheme in ("http", "https") and bool(url.host)


def _env_file_row(env_file: dict[str, str] | None) -> Row:
    if env_file is None:
        return Row(
            ".env", "pass", "no .env file at the repository root; using the process environment"
        )
    if not env_file:
        return Row(".env", "pass", "found but empty; using the process environment")
    return Row(".env", "pass", f"loaded {len(env_file)} variable(s): {', '.join(sorted(env_file))}")


def _base_url_row(env_file: dict[str, str] | None, secrets: tuple[str, ...]) -> Row:
    value, source = _lookup("ENGENIA_LLM_BASE_URL", env_file)
    if not value:
        return Row("ENGENIA_LLM_BASE_URL", "skip", "not set")
    if not _valid_url(value):
        return Row(
            "ENGENIA_LLM_BASE_URL",
            "fail",
            _clean(f"set but not an http(s) URL: {value}", secrets),
        )
    if source == "process environment":
        return Row("ENGENIA_LLM_BASE_URL", "pass", f"set: {value}")
    return Row(
        "ENGENIA_LLM_BASE_URL", "warn", f"set: {value} (in .env, not exported to this process)"
    )


def _api_key_row(env_file: dict[str, str] | None) -> Row:
    value, source = _lookup("ENGENIA_LLM_API_KEY", env_file)
    if not value:
        return Row("ENGENIA_LLM_API_KEY", "skip", "not set")
    if source == "process environment":
        return Row("ENGENIA_LLM_API_KEY", "pass", "set (value never printed)")
    return Row(
        "ENGENIA_LLM_API_KEY",
        "warn",
        "set in .env, not exported to this process (value never printed)",
    )


def _model_row(name: str, value: str, source: str, live: bool) -> Row:
    if not value:
        if live:
            return Row(name, "fail", "not set (required for live model calls)")
        return Row(name, "skip", "not set")
    family = model_family(value)
    if source == "process environment":
        return Row(name, "pass", f"set: {value} (family {family!r})")
    return Row(name, "warn", f"set: {value} (family {family!r}; in .env, not exported)")


def _family_row(design: str, verify: str, secrets: tuple[str, ...]) -> Row:
    if not design or not verify:
        return Row("model families", "skip", "skipped: both model names are needed to compare")
    design_family, verify_family = model_family(design), model_family(verify)
    if design_family != verify_family:
        return Row("model families", "pass", f"different families: {design!r} vs {verify!r}")
    detail = (
        f"{design!r} and {verify!r} share the family {design_family!r}: a model must not check "
        "its own family's work. Choose models from different families, or set "
        f"{ALLOW_SAME_FAMILY_ENV}=1 to override"
    )
    if os.environ.get(ALLOW_SAME_FAMILY_ENV, "").strip() == "1":
        return Row("model families", "warn", _clean(f"{detail} (it is overridden now)", secrets))
    return Row("model families", "fail", _clean(detail, secrets))


def _mode_row(
    key_env: str, base_env: str, env_file: dict[str, str] | None, secrets: tuple[str, ...]
) -> Row:
    if key_env and base_env:
        return Row(
            "model mode", "pass", "live model calls: agents will use the configured endpoint"
        )
    missing = [
        name
        for name, value in (("ENGENIA_LLM_API_KEY", key_env), ("ENGENIA_LLM_BASE_URL", base_env))
        if not value
    ]
    detail = (
        f"deterministic fallback mode: {' and '.join(missing)} not set in this process; "
        "design agents run their deterministic builders instead of model calls"
    )
    if any((env_file or {}).get(name, "").strip() for name in missing):
        detail += (
            "; a .env value exists but is not exported "
            "(the pipeline reads the process environment)"
        )
    return Row("model mode", "warn", _clean(detail, secrets))


def _probe_row(
    check: str,
    model: str,
    *,
    live: bool,
    client: httpx.Client | None,
    sleep: Callable[[float], None],
    secrets: tuple[str, ...],
) -> Row:
    """One tiny tool-calling request, through the same `chat` client the agents use."""
    if not live:
        return Row(check, "skip", "skipped: no usable endpoint and API key in this process")
    if not model:
        return Row(check, "skip", "skipped: model name not set in this process")
    try:
        response = chat(model, PROBE_MESSAGES, PROBE_TOOLS, 0, client=client, sleep=sleep)
        message = message_of(response)
    except Exception as exc:  # chat and message_of raise LLMError for every failure mode
        return Row(check, "fail", _clean(f"{model}: {exc}", secrets))
    if message.get("tool_calls"):
        return Row(check, "pass", f"{model}: tool call returned")
    return Row(check, "pass", f"{model}: replied without a tool call")


def _run_command_default(argv: list[str]) -> tuple[bool, str]:
    if shutil.which(argv[0]) is None:
        return False, f"{argv[0]}: not found on PATH"
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{argv[0]}: {type(exc).__name__}: {exc}"
    output = (done.stdout + done.stderr).strip()
    if done.returncode == 0:
        return True, output
    return (
        False,
        output or f"{argv[0]} exited with no output (return code {done.returncode})",
    )


def _command_row(
    check: str, candidates: list[list[str]], run_command: CommandRunner, secrets: tuple[str, ...]
) -> Row:
    """Pass on the first candidate command that runs; otherwise report every failure."""
    failures: list[str] = []
    for argv in candidates:
        ok, output = run_command(argv)
        if ok:
            first = output.splitlines()[0].strip() if output.strip() else "ok"
            return Row(check, "pass", _clean(first, secrets))
        failures.append(output)
    return Row(check, "fail", _clean("; ".join(failures), secrets))


def _version_of(module: ModuleType) -> str:
    for name in ("__version__", "GMSH_API_VERSION", "version"):
        value = getattr(module, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, tuple) and value:
            return ".".join(str(part) for part in value)
    return "unknown"


def _module_state(module_name: str, import_module: ModuleImporter) -> tuple[RowResult, str]:
    try:
        module = import_module(module_name)
    except Exception as exc:  # the reason is in the exception; nothing here may crash the doctor
        return "fail", f"import failed: {type(exc).__name__}: {exc}"
    return "pass", f"import ok (version {_version_of(module)})"


def _gmsh_row(import_module: ModuleImporter, secrets: tuple[str, ...]) -> Row:
    result, detail = _module_state("gmsh", import_module)
    if result == "fail":
        libglu = ctypes.util.find_library("GLU")
        detail = f"{detail}; libGLU: {libglu or 'not found'}"
        if libglu is None:
            detail += "; hint: install libGLU (Debian: libglu1-mesa) and the gmsh SDK"
    return Row("gmsh + libGLU", result, _clean(detail, secrets))


def _module_row(name: str, import_module: ModuleImporter, secrets: tuple[str, ...]) -> Row:
    result, detail = _module_state(name, import_module)
    return Row(name, result, _clean(detail, secrets))


def _render(root: Path, rows: list[Row]) -> str:
    width = max(len(row.check) for row in rows)
    lines = [
        "engenia doctor",
        f"root: {root}",
        "",
        f"{'check'.ljust(width)}  result  detail",
        f"{'-' * width}  ------  ------",
    ]
    for row in rows:
        lines.append(f"{row.check.ljust(width)}  {row.result.upper():<6}  {row.detail}")
    counts = {name: 0 for name in ("pass", "warn", "fail", "skip")}
    for row in rows:
        counts[row.result] += 1
    verdict = "exit 1 (fix the failing checks)" if counts["fail"] else "exit 0"
    lines.append("")
    lines.append(
        f"summary: {counts['pass']} pass, {counts['warn']} warn, {counts['skip']} skip, "
        f"{counts['fail']} fail -> {verdict}"
    )
    return "\n".join(lines)


def run_doctor(
    root: Path,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    run_command: CommandRunner | None = None,
    import_module: ModuleImporter | None = None,
) -> DoctorResult:
    """Run every check and return the rows, the rendered table and the exit code.

    `client`, `sleep`, `run_command` and `import_module` exist so tests can run without a
    network, real waiting, installed tools or heavy imports; the defaults do the real work.
    """
    run_command = run_command or _run_command_default
    import_module = import_module or importlib.import_module
    env_file = read_env_file(root)

    key_env = os.environ.get("ENGENIA_LLM_API_KEY", "").strip()
    base_env = os.environ.get("ENGENIA_LLM_BASE_URL", "").strip()
    live = bool(key_env) and _valid_url(base_env)
    design_env = os.environ.get("ENGENIA_DESIGN_MODEL", "").strip()
    verify_env = os.environ.get("ENGENIA_VERIFY_MODEL", "").strip()

    file_key = (env_file or {}).get("ENGENIA_LLM_API_KEY", "").strip()
    secrets = tuple({value for value in (key_env, file_key) if len(value) >= MIN_REDACT_CHARS})

    design, design_source = _lookup("ENGENIA_DESIGN_MODEL", env_file)
    verify, verify_source = _lookup("ENGENIA_VERIFY_MODEL", env_file)

    probes: dict[str, Row] = {}

    def probe(label: str, model: str) -> Row:
        if model not in probes:
            probes[model] = _probe_row(
                label, model, live=live, client=client, sleep=sleep, secrets=secrets,
            )
        row = probes[model]
        return Row(label, row.result, row.detail)

    rows = [
        _env_file_row(env_file),
        _base_url_row(env_file, secrets),
        _api_key_row(env_file),
        _model_row(
            "ENGENIA_DESIGN_MODEL", design, design_source,
            live and any(not agent_model(agent) for agent in DESIGN_AGENTS),
        ),
        _model_row("ENGENIA_VERIFY_MODEL", verify, verify_source, live),
        _family_row(design, verify, secrets),
        _mode_row(key_env, base_env, env_file, secrets),
        probe("design model call", design_env),
        probe("verify model call", verify_env),
        _command_row("uv", [["uv", "--version"]], run_command, secrets),
        _command_row(
            "pio", [["pio", "--version"], ["platformio", "--version"]], run_command, secrets
        ),
        _gmsh_row(import_module, secrets),
        _module_row("capytaine", import_module, secrets),
    ]
    for agent in DESIGN_AGENTS:
        variable = f"ENGENIA_MODEL_{agent.upper()}"
        override, source = _lookup(variable, env_file)
        model = override or design
        source = source if override else design_source
        rows.append(_model_row(f"{agent} model", model, source, live))
        family = _family_row(model, verify, secrets)
        rows.append(Row(f"{agent} model families", family.result, family.detail))
        rows.append(probe(f"{agent} model call", agent_model(agent)))
    for agent in ("verify_plan", "final"):
        rows.append(_model_row(f"{agent} model", verify, verify_source, live))
    exit_code = 1 if any(row.result == "fail" for row in rows) else 0
    return DoctorResult(rows=tuple(rows), text=_render(root, rows), exit_code=exit_code)
