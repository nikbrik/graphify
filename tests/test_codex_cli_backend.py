"""Tests for the `codex-cli` backend.

Mocks subprocess.run + shutil.which so the suite runs on CI without
the `codex` binary or a live network call.
"""
from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from graphify import llm
from graphify import rate_limit

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


def _write_parallel_files(tmp_path, count=3):
    files = []
    for i in range(count):
        path = tmp_path / f"doc{i}.md"
        path.write_text(f"# Doc {i}\n")
        files.append(path)
    return files


def _fake_chunk_result(chunk):
    name = Path(chunk[0]).stem
    return {
        "nodes": [{"id": name, "label": name, "file_type": "document", "source_file": f"{name}.md"}],
        "edges": [],
        "hyperedges": [],
        "input_tokens": 1,
        "output_tokens": 1,
    }


def test_extract_corpus_parallel_codex_cli_serial_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("GRAPHIFY_CODEX_CLI_PARALLEL", raising=False)
    files = _write_parallel_files(tmp_path)
    calls = []

    def fake_adaptive(chunk, **kwargs):
        calls.append([p.name for p in chunk])
        return _fake_chunk_result(chunk)

    def forbidden_pool(*args, **kwargs):
        raise AssertionError("codex-cli default path must be serial")

    monkeypatch.setattr(llm, "_extract_with_adaptive_retry", fake_adaptive)
    monkeypatch.setattr(llm, "ThreadPoolExecutor", forbidden_pool)

    result = llm.extract_corpus_parallel(
        files,
        backend="codex-cli",
        root=tmp_path,
        token_budget=None,
        chunk_size=1,
        max_concurrency=3,
    )

    assert calls == [["doc0.md"], ["doc1.md"], ["doc2.md"]]
    assert len(result["nodes"]) == 3


def test_extract_corpus_parallel_codex_cli_parallel_opt_in(monkeypatch, tmp_path):
    files = _write_parallel_files(tmp_path)
    monkeypatch.setenv("GRAPHIFY_CODEX_CLI_PARALLEL", "1")
    created_workers = []

    class FakePool:
        def __init__(self, max_workers):
            created_workers.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args, **kwargs):
            fut = Future()
            try:
                fut.set_result(fn(*args, **kwargs))
            except BaseException as exc:
                fut.set_exception(exc)
            return fut

    monkeypatch.setattr(llm, "_extract_with_adaptive_retry", lambda chunk, **kwargs: _fake_chunk_result(chunk))
    monkeypatch.setattr(llm, "ThreadPoolExecutor", FakePool)

    result = llm.extract_corpus_parallel(
        files,
        backend="codex-cli",
        root=tmp_path,
        token_budget=None,
        chunk_size=1,
        max_concurrency=3,
    )

    assert created_workers == [3]
    assert len(result["nodes"]) == 3


def test_extract_corpus_parallel_retry_accounting_preserves_chunks(monkeypatch, tmp_path, capsys):
    files = _write_parallel_files(tmp_path)
    monkeypatch.delenv("GRAPHIFY_CODEX_CLI_PARALLEL", raising=False)
    calls = {"n": 0}

    def fake_adaptive(chunk, **kwargs):
        if calls["n"] == 0:
            rate_limit._record_thread_retry(0.0)
            rate_limit._record_global_retry(0.0)
        calls["n"] += 1
        return _fake_chunk_result(chunk)

    monkeypatch.setattr(llm, "_extract_with_adaptive_retry", fake_adaptive)

    result = llm.extract_corpus_parallel(
        files,
        backend="codex-cli",
        root=tmp_path,
        token_budget=None,
        chunk_size=1,
        max_concurrency=3,
    )

    assert len(result["nodes"]) == 3
    assert result["failed_chunks"] == 0
    assert result["rate_limit_retries"] == 1
    assert result["rate_limit_recovered_chunks"] == 1
    assert "1 chunk(s) recovered after 1 retries" in capsys.readouterr().err
