"""Tests for `engenia doctor`: mocked HTTP, injected tool checks, no network and no heavy imports.

The model probes go through `run_doctor(client=...)` with an httpx.MockTransport, exactly like
the harness tests do; tool and import checks get fake runners so the suite never needs uv, pio,
gmsh or capytaine to be installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.evidence.doctor import DoctorResult, Row, read_env_file, run_doctor
from engenia.harness.llm import text_response, tool_call_response

KEY = "sk-doctor-test-key-000"
BASE_URL = "https://llm.test/v1/"
DESIGN_MODEL = "Qwen/Qwen3-235B"
VERIFY_MODEL = "Meta/Llama-3.3-70B"

ENV_VARS = (
    "ENGENIA_LLM_BASE_URL",
    "ENGENIA_LLM_API_KEY",
    "ENGENIA_DESIGN_MODEL",
    "ENGENIA_VERIFY_MODEL",
    "ENGENIA_ALLOW_SAME_FAMILY",
)


def row_of(result: DoctorResult, check: str) -> Row:
    return next(row for row in result.rows if row.check == check)


def _sleep_noop(_seconds: float) -> None:
    return None


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _tools_ok(_argv: list[str]) -> tuple[bool, str]:
    return True, "tool version 9.9.9"


def _imports_ok(name: str) -> ModuleType:
    module = ModuleType(name)
    module.__version__ = "9.9.9"
    return module


def _tool_call_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=tool_call_response(("doctor_ping", {})))


def _text_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=text_response("I am a text model"))


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def endpoint(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENGENIA_LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("ENGENIA_LLM_API_KEY", KEY)
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", DESIGN_MODEL)
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", VERIFY_MODEL)


# --- .env parsing -----------------------------------------------------------------------------


def test_read_env_file_parses_quotes_exports_and_comments(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "# model access\n"
        "export ENGENIA_LLM_BASE_URL=https://api.studio.nebius.com/v1\n"
        'ENGENIA_LLM_API_KEY="sk-abc12345"\n'
        "\n"
        "ENGENIA_DESIGN_MODEL=Qwen/Qwen3-235B\n"
        "ENGENIA_VERIFY_MODEL='Meta/Llama-3.3-70B'\n"
        "BROKEN LINE WITHOUT EQUALS\n",
        encoding="utf-8",
    )
    assert read_env_file(tmp_path) == {
        "ENGENIA_LLM_BASE_URL": "https://api.studio.nebius.com/v1",
        "ENGENIA_LLM_API_KEY": "sk-abc12345",
        "ENGENIA_DESIGN_MODEL": "Qwen/Qwen3-235B",
        "ENGENIA_VERIFY_MODEL": "Meta/Llama-3.3-70B",
    }


def test_read_env_file_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_env_file(tmp_path) is None


# --- no configuration: deterministic fallback mode --------------------------------------------


def test_no_configuration_reports_fallback_as_warning_not_failure(
    clean_env: None, tmp_path: Path
) -> None:
    result = run_doctor(
        tmp_path, sleep=_sleep_noop, run_command=_tools_ok, import_module=_imports_ok
    )

    assert result.exit_code == 0
    mode = row_of(result, "model mode")
    assert mode.result == "warn"
    assert "deterministic fallback mode" in mode.detail
    assert row_of(result, "ENGENIA_LLM_API_KEY").result == "skip"
    assert row_of(result, "ENGENIA_LLM_BASE_URL").result == "skip"
    assert row_of(result, "ENGENIA_DESIGN_MODEL").result == "skip"
    assert row_of(result, "design model call").result == "skip"
    assert row_of(result, "verify model call").result == "skip"
    assert not any(row.result == "fail" for row in result.rows)
    assert "WARN" in result.text


def test_env_only_configuration_warns_and_skips_probes(clean_env: None, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "ENGENIA_LLM_BASE_URL=https://llm.test/v1\n"
        f"ENGENIA_LLM_API_KEY={KEY}\n"
        f"ENGENIA_DESIGN_MODEL={DESIGN_MODEL}\n"
        f"ENGENIA_VERIFY_MODEL={VERIFY_MODEL}\n",
        encoding="utf-8",
    )

    result = run_doctor(
        tmp_path, sleep=_sleep_noop, run_command=_tools_ok, import_module=_imports_ok
    )

    assert result.exit_code == 0
    assert row_of(result, "ENGENIA_LLM_BASE_URL").result == "warn"
    assert "not exported" in row_of(result, "ENGENIA_LLM_BASE_URL").detail
    assert row_of(result, "ENGENIA_LLM_API_KEY").result == "warn"
    assert row_of(result, "ENGENIA_DESIGN_MODEL").result == "warn"
    assert row_of(result, "model families").result == "pass"  # the .env pair itself is sound
    assert row_of(result, "model mode").result == "warn"
    assert "deterministic fallback mode" in row_of(result, "model mode").detail
    assert row_of(result, "design model call").result == "skip"
    assert row_of(result, "verify model call").result == "skip"


# --- live mode: the tiny tool-calling probes ---------------------------------------------------


def test_live_configuration_probes_both_models_with_a_tiny_tool_call(
    endpoint: None, tmp_path: Path
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=tool_call_response(("doctor_ping", {"note": "ok"})))

    result = run_doctor(
        tmp_path, client=_client(handler), sleep=_sleep_noop, run_command=_tools_ok,
        import_module=_imports_ok,
    )

    assert result.exit_code == 0
    assert row_of(result, "model mode").result == "pass"
    assert row_of(result, "model families").result == "pass"
    assert row_of(result, "design model call").result == "pass"
    assert "tool call returned" in row_of(result, "design model call").detail
    assert row_of(result, "verify model call").result == "pass"
    assert [json.loads(request.content)["model"] for request in requests] == [
        DESIGN_MODEL,
        VERIFY_MODEL,
    ]
    for request in requests:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "doctor_ping"  # a tool-calling request
        assert len(body["messages"]) <= 3  # tiny
        assert str(request.url) == BASE_URL + "chat/completions"
        assert request.headers["authorization"] == f"Bearer {KEY}"
    assert result.text.startswith("engenia doctor")
    assert "check" in result.text and "summary:" in result.text


def test_probe_passes_without_a_tool_call(endpoint: None, tmp_path: Path) -> None:
    result = run_doctor(
        tmp_path,
        client=_client(_text_handler),
        sleep=_sleep_noop,
        run_command=_tools_ok,
        import_module=_imports_ok,
    )

    assert result.exit_code == 0
    assert row_of(result, "design model call").result == "pass"
    assert "replied without a tool call" in row_of(result, "design model call").detail


def test_failed_model_call_fails_the_doctor(endpoint: None, tmp_path: Path) -> None:
    responses = iter(
        [
            httpx.Response(401, json={"error": "bad key"}),
            httpx.Response(200, json=tool_call_response(("doctor_ping", {}))),
        ]
    )

    result = run_doctor(
        tmp_path,
        client=_client(lambda request: next(responses)),
        sleep=_sleep_noop,
        run_command=_tools_ok,
        import_module=_imports_ok,
    )

    assert result.exit_code == 1
    design_call = row_of(result, "design model call")
    verify_call = row_of(result, "verify model call")
    assert design_call.result == "fail"
    assert "HTTP 401" in design_call.detail
    assert verify_call.result == "pass"


def test_api_key_is_never_printed_even_when_the_server_echoes_it(
    endpoint: None, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"bad key: {request.headers['authorization']}")

    result = run_doctor(
        tmp_path, client=_client(handler), sleep=_sleep_noop, run_command=_tools_ok,
        import_module=_imports_ok,
    )

    design_call = row_of(result, "design model call")
    assert design_call.result == "fail"
    assert KEY not in result.text
    assert KEY not in design_call.detail
    assert "***" in design_call.detail


# --- model-family guard ------------------------------------------------------------------------


def test_same_family_fails_the_doctor(
    endpoint: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Qwen/Qwen2.5-72B")

    result = run_doctor(
        tmp_path,
        client=_client(_tool_call_handler),
        sleep=_sleep_noop,
        run_command=_tools_ok,
        import_module=_imports_ok,
    )

    families = row_of(result, "model families")
    assert families.result == "fail"
    assert "'qwen'" in families.detail
    assert "ENGENIA_ALLOW_SAME_FAMILY" in families.detail
    assert result.exit_code == 1
    assert row_of(result, "design model call").result == "pass"  # the endpoint itself works


def test_same_family_warns_when_overridden(
    endpoint: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Qwen/Qwen2.5-72B")
    monkeypatch.setenv("ENGENIA_ALLOW_SAME_FAMILY", "1")

    result = run_doctor(
        tmp_path,
        client=_client(_tool_call_handler),
        sleep=_sleep_noop,
        run_command=_tools_ok,
        import_module=_imports_ok,
    )

    assert row_of(result, "model families").result == "warn"
    assert result.exit_code == 0


# --- incomplete live configuration ---------------------------------------------------------------


def test_live_mode_requires_model_names(clean_env: None, monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    monkeypatch.setenv("ENGENIA_LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("ENGENIA_LLM_API_KEY", KEY)

    result = run_doctor(
        tmp_path, sleep=_sleep_noop, run_command=_tools_ok, import_module=_imports_ok
    )

    assert row_of(result, "ENGENIA_DESIGN_MODEL").result == "fail"
    assert row_of(result, "ENGENIA_VERIFY_MODEL").result == "fail"
    assert row_of(result, "model families").result == "skip"
    assert row_of(result, "design model call").result == "skip"
    assert row_of(result, "verify model call").result == "skip"
    assert result.exit_code == 1


def test_invalid_base_url_fails_and_skips_probes(clean_env: None, monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    monkeypatch.setenv("ENGENIA_LLM_BASE_URL", "nebius-api")  # no scheme
    monkeypatch.setenv("ENGENIA_LLM_API_KEY", KEY)
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", DESIGN_MODEL)
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", VERIFY_MODEL)

    result = run_doctor(
        tmp_path, sleep=_sleep_noop, run_command=_tools_ok, import_module=_imports_ok
    )

    assert row_of(result, "ENGENIA_LLM_BASE_URL").result == "fail"
    assert row_of(result, "design model call").result == "skip"
    assert row_of(result, "verify model call").result == "skip"
    assert result.exit_code == 1


# --- local tools --------------------------------------------------------------------------------


def test_missing_tools_and_modules_fail(clean_env: None, tmp_path: Path) -> None:
    def run_command(argv: list[str]) -> tuple[bool, str]:
        return False, f"{argv[0]}: not found on PATH"

    def import_module(name: str) -> ModuleType:
        raise ImportError(f"No module named {name!r}")

    result = run_doctor(
        tmp_path, sleep=_sleep_noop, run_command=run_command, import_module=import_module
    )

    for check in ("uv", "pio", "gmsh + libGLU", "capytaine"):
        assert row_of(result, check).result == "fail"
    assert "libGLU" in row_of(result, "gmsh + libGLU").detail
    assert result.exit_code == 1


def test_pio_falls_back_to_platformio(clean_env: None, tmp_path: Path) -> None:
    def run_command(argv: list[str]) -> tuple[bool, str]:
        if argv[0] == "pio":
            return False, "pio: not found on PATH"
        return True, "PlatformIO Core, version 6.1.18"

    result = run_doctor(
        tmp_path, sleep=_sleep_noop, run_command=run_command, import_module=_imports_ok
    )

    pio = row_of(result, "pio")
    assert pio.result == "pass"
    assert "PlatformIO" in pio.detail


def test_installed_tools_and_modules_pass(endpoint: None, tmp_path: Path) -> None:
    result = run_doctor(
        tmp_path,
        client=_client(_tool_call_handler),
        sleep=_sleep_noop,
        run_command=_tools_ok,
        import_module=_imports_ok,
    )

    for check in ("uv", "pio", "gmsh + libGLU", "capytaine"):
        row = row_of(result, check)
        assert row.result == "pass"
        assert "9.9.9" in row.detail
    assert result.exit_code == 0


# --- CLI wiring ---------------------------------------------------------------------------------


def test_cli_doctor_echoes_the_table_and_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_roots: list[Path] = []

    def fake_run_doctor(root: Path, **_kwargs: object) -> DoctorResult:
        seen_roots.append(root)
        return DoctorResult(rows=(), text="fake doctor table", exit_code=1)

    monkeypatch.setattr("engenia.evidence.doctor.run_doctor", fake_run_doctor)

    result = CliRunner().invoke(app, ["doctor"])

    from engenia.core.paths import find_root

    assert seen_roots == [find_root()]
    assert result.exit_code == 1
    assert "fake doctor table" in result.stdout


def test_cli_doctor_exit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_doctor(_root: Path, **_kwargs: object) -> DoctorResult:
        return DoctorResult(rows=(), text="all good", exit_code=0)

    monkeypatch.setattr("engenia.evidence.doctor.run_doctor", fake_run_doctor)

    assert CliRunner().invoke(app, ["doctor"]).exit_code == 0


def test_cli_lists_the_doctor_command() -> None:
    assert "doctor" in CliRunner().invoke(app, ["--help"]).stdout
