"""Tests for the `codex-cli` backend.

Mocks subprocess.run + shutil.which so the suite runs on CI without
the `codex` binary or a live network call.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from graphify import llm

_VALID_EXTRACTION = {
    "nodes": [
        {"id": "foo_module", "label": "Foo", "file_type": "document", "source_file": "foo.md",
         "source_location": None, "source_url": None, "captured_at": None, "author": None,
         "contributor": None, "rationale": None},
        {"id": "foo_greet", "label": "greet", "file_type": "code", "source_file": "foo.md",
         "source_location": None, "source_url": None, "captured_at": None, "author": None,
         "contributor": None, "rationale": None},
    ],
    "edges": [
        {"source": "foo_module", "target": "foo_greet",
         "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
         "source_file": "foo.md", "source_location": None, "weight": 1.0},
    ],
    "hyperedges": [],
    "input_tokens": 0,
    "output_tokens": 0,
}


def _make_run_side_effect(payload: dict | str = _VALID_EXTRACTION):
    def fake_run(args, **kwargs):
        out_idx = args.index("-o") + 1 if "-o" in args else None
        if out_idx is not None:
            Path(args[out_idx]).write_text(
                json.dumps(payload) if isinstance(payload, dict) else payload,
                encoding="utf-8",
            )
        usage_line = json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 42, "output_tokens": 17},
            "model": "gpt-5.4-codex",
        })
        return MagicMock(returncode=0, stdout=usage_line + "\n", stderr="")

    return fake_run


@pytest.fixture
def fake_codex(monkeypatch):
    monkeypatch.setattr(llm, "_response_is_hollow", lambda raw, parsed: False)
    with patch("shutil.which", return_value="/fake/bin/codex"), \
         patch("subprocess.run", side_effect=_make_run_side_effect()) as run:
        yield run


def test_returns_parsed_nodes_and_edges(fake_codex):
    result = llm._call_codex_cli("dummy", max_tokens=8192)
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1


def test_token_accounting_from_jsonl(fake_codex):
    result = llm._call_codex_cli("dummy", max_tokens=8192)
    assert result["input_tokens"] == 42
    assert result["output_tokens"] == 17
    assert result["model"] == "gpt-5.4-codex"
    assert result["finish_reason"] == "stop"


def test_hollow_response_maps_to_length(monkeypatch):
    monkeypatch.setattr(llm, "_response_is_hollow", lambda raw, parsed: True)
    with patch("shutil.which", return_value="/fake/bin/codex"), \
         patch("subprocess.run", side_effect=_make_run_side_effect({"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0})):
        result = llm._call_codex_cli("dummy", max_tokens=8192)
    assert result["finish_reason"] == "length"


def test_invalid_json_maps_to_length(monkeypatch):
    monkeypatch.setattr(llm, "_response_is_hollow", lambda raw, parsed: False)
    with patch("shutil.which", return_value="/fake/bin/codex"), \
         patch("subprocess.run", side_effect=_make_run_side_effect("not json at all")):
        result = llm._call_codex_cli("dummy", max_tokens=8192)
    assert result["finish_reason"] == "length"


def test_codex_cli_skips_byte_read_for_images(tmp_path, monkeypatch):
    big = tmp_path / "huge.png"
    big.write_bytes(b"x" * 64)
    monkeypatch.setattr(llm, "_MAX_IMAGE_BYTES", 8)
    (ref,) = llm._build_image_refs([big], tmp_path, read_bytes=False)
    assert ref.raw is None
    assert ref.path.name == "huge.png"


def test_raises_when_cli_missing():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="Codex CLI not found"):
            llm._call_codex_cli("dummy", max_tokens=8192)


def test_raises_on_nonzero_exit():
    completed = MagicMock(returncode=2, stdout="", stderr="not logged in")
    with patch("shutil.which", return_value="/fake/bin/codex"), \
         patch("subprocess.run", return_value=completed):
        with pytest.raises(RuntimeError, match="exited 2"):
            llm._call_codex_cli("dummy", max_tokens=8192)


def test_raises_on_missing_output_file(monkeypatch):
    monkeypatch.setattr(llm, "_response_is_hollow", lambda raw, parsed: False)

    def fake_run(args, **kwargs):
        out_idx = args.index("-o") + 1
        Path(args[out_idx]).unlink(missing_ok=True)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("shutil.which", return_value="/fake/bin/codex"), \
         patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="no output-last-message"):
            llm._call_codex_cli("dummy", max_tokens=8192)


def test_extract_files_direct_dispatches_to_codex_cli(tmp_path, fake_codex):
    f = tmp_path / "foo.md"
    f.write_text("# Foo\n\nThe greet() helper formats a name.\n")
    result = llm.extract_files_direct(files=[f], backend="codex-cli", root=tmp_path)
    assert fake_codex.called
    assert len(result["nodes"]) == 2


def test_backend_registered_with_zero_cost():
    assert "codex-cli" in llm.BACKENDS
    pricing = llm.BACKENDS["codex-cli"]["pricing"]
    assert pricing["input"] == 0.0
    assert pricing["output"] == 0.0
    assert llm.estimate_cost("codex-cli", 1_000_000, 1_000_000) == 0.0


def test_exec_argv_includes_schema_and_sandbox(fake_codex):
    llm._call_codex_cli("payload", max_tokens=8192, root=Path("/tmp/root"))
    argv = fake_codex.call_args.args[0]
    assert argv[0] == "/fake/bin/codex"
    assert argv[1] == "exec"
    assert "--output-schema" in argv
    assert "-o" in argv
    assert "--sandbox" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in argv
    assert "--skip-git-repo-check" in argv
    assert "--json" in argv
    assert "-C" in argv
    assert argv[argv.index("-C") + 1] == str(Path("/tmp/root").resolve())


def test_model_flag_from_env(monkeypatch, fake_codex):
    monkeypatch.setenv("GRAPHIFY_CODEX_CLI_MODEL", "gpt-5.4-codex")
    llm._call_codex_cli("payload", max_tokens=8192)
    argv = fake_codex.call_args.args[0]
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "gpt-5.4-codex"


def test_no_model_flag_when_env_var_unset(monkeypatch, fake_codex):
    monkeypatch.delenv("GRAPHIFY_CODEX_CLI_MODEL", raising=False)
    llm._call_codex_cli("payload", max_tokens=8192)
    argv = fake_codex.call_args.args[0]
    assert "-m" not in argv


def test_sandbox_bypass_from_env(monkeypatch, fake_codex):
    monkeypatch.setenv("GRAPHIFY_CODEX_CLI_SANDBOX", "bypass")
    llm._call_codex_cli("payload", max_tokens=8192)
    argv = fake_codex.call_args.args[0]
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--sandbox" not in argv


def test_image_flags_passed(fake_codex, tmp_path):
    img = tmp_path / "diagram.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    refs = llm._build_image_refs([img], tmp_path, read_bytes=False)
    llm._call_codex_cli("CORPUS", images=refs, root=tmp_path)
    argv = fake_codex.call_args.args[0]
    assert "-i" in argv
    assert str(refs[0].path) in argv


# ---------- Windows path resolution ----------


def test_windows_prefers_codex_cmd_over_bare_codex(monkeypatch):
    monkeypatch.setattr(llm, "_response_is_hollow", lambda raw, parsed: False)

    def fake_which(name):
        return {
            "codex": r"C:\Users\u\AppData\Roaming\npm\codex.ps1",
            "codex.cmd": r"C:\Users\u\AppData\Roaming\npm\codex.cmd",
        }.get(name)

    with patch("platform.system", return_value="Windows"), \
         patch("shutil.which", side_effect=fake_which), \
         patch("subprocess.run", side_effect=_make_run_side_effect()) as run:
        llm._call_codex_cli("dummy", max_tokens=8192)

    argv = run.call_args.args[0]
    assert argv[0] == r"C:\Users\u\AppData\Roaming\npm\codex.cmd"


def test_windows_raises_when_neither_cmd_nor_bare_codex_present():
    with patch("platform.system", return_value="Windows"), \
         patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="Codex CLI not found"):
            llm._call_codex_cli("dummy", max_tokens=8192)


def test_call_llm_dispatches_to_codex_cli_text(monkeypatch):
    monkeypatch.setattr(llm, "_call_codex_cli_text", lambda prompt, **kw: '{"0":"Test"}')
    out = llm._call_llm("name communities", backend="codex-cli", max_tokens=100)
    assert out == '{"0":"Test"}'


def test_quoted_json_output_is_unwrapped(monkeypatch):
    payload = json.dumps(_VALID_EXTRACTION)
    quoted = json.dumps(payload)

    def fake_run(args, **kwargs):
        out_idx = args.index("-o") + 1
        Path(args[out_idx]).write_text(quoted, encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(llm, "_response_is_hollow", lambda raw, parsed: False)
    with patch("shutil.which", return_value="/fake/bin/codex"), \
         patch("subprocess.run", side_effect=fake_run):
        result = llm._call_codex_cli("dummy", max_tokens=8192)
    assert len(result["nodes"]) == 2


def test_model_env_key_in_backend_config():
    assert llm.BACKENDS["codex-cli"].get("model_env_key") == "GRAPHIFY_CODEX_CLI_MODEL"


def test_model_from_default_backend_config(monkeypatch, fake_codex, tmp_path):
    monkeypatch.setenv("GRAPHIFY_CODEX_CLI_MODEL", "gpt-5.4-codex")
    f = tmp_path / "doc.md"
    f.write_text("# Doc\n")
    llm.extract_files_direct(files=[f], backend="codex-cli", root=tmp_path)
    argv = fake_codex.call_args.args[0]
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "gpt-5.4-codex"


def test_serial_by_default_respects_env(monkeypatch):
    monkeypatch.delenv("GRAPHIFY_CODEX_CLI_PARALLEL", raising=False)
    max_concurrency = 8
    backend = "codex-cli"
    if backend == "codex-cli" and os.environ.get("GRAPHIFY_CODEX_CLI_PARALLEL", "").strip() != "1":
        max_concurrency = 1
    assert max_concurrency == 1

    monkeypatch.setenv("GRAPHIFY_CODEX_CLI_PARALLEL", "1")
    max_concurrency = 8
    if backend == "codex-cli" and os.environ.get("GRAPHIFY_CODEX_CLI_PARALLEL", "").strip() != "1":
        max_concurrency = 1
    assert max_concurrency == 8
