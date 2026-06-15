"""Tests for `graphify extract` CLI dispatch path in graphify.__main__."""
from __future__ import annotations

import json

import pytest

import graphify.__main__ as mainmod


def _make_corpus(tmp_path):
    """Minimal corpus: one Go code file + one Markdown doc.

    Both file types are needed so semantic extraction is requested
    (docs path triggers the LLM step we want to assert against).
    """
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
    (tmp_path / "README.md").write_text("# Notes\nThe main function entry point.\n")
    return tmp_path


def test_extract_exits_nonzero_when_all_semantic_chunks_fail(
    monkeypatch, tmp_path, capsys
):
    """When every semantic chunk errors (e.g. backend SDK not installed),
    the CLI must exit non-zero instead of silently writing an AST-only graph.

    The bug this guards: `pip install graphifyy` doesn't pull in `anthropic`,
    so `graphify extract --backend claude` would print per-chunk errors and
    still exit 0 with a graph.json. Callers checking exit status saw success.
    """
    corpus = _make_corpus(tmp_path)
    out_dir = tmp_path / "out"

    # Stub the API-key check so the backend gate doesn't reject before we
    # reach the semantic-extraction step.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

    # Patch extract_corpus_parallel to simulate "all chunks failed":
    # return an empty merged accumulator without ever invoking on_chunk_done.
    # This matches the real behavior of extract_corpus_parallel when every
    # chunk raises (the per-chunk failures print to stderr and the loop
    # continues without calling the success callback).
    def _all_chunks_failed(paths, **kwargs):
        return {
            "nodes": [],
            "edges": [],
            "hyperedges": [],
            "input_tokens": 0,
            "output_tokens": 0,
        }

    monkeypatch.setattr(
        "graphify.llm.extract_corpus_parallel", _all_chunks_failed
    )
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "extract", str(corpus), "--backend", "claude",
         "--out", str(out_dir)],
    )

    with pytest.raises(SystemExit) as exc_info:
        mainmod.main()

    assert exc_info.value.code == 1, (
        f"expected exit code 1 when all semantic chunks fail, "
        f"got {exc_info.value.code}"
    )

    stderr = capsys.readouterr().err
    assert "all semantic chunks failed" in stderr
    assert "claude" in stderr

    # No graph.json should have been written - the failure must abort before
    # the merge/cluster/write phase, not after.
    assert not (out_dir / "graphify-out" / "graph.json").exists(), (
        "graph.json must not be written when semantic extraction fails"
    )


def test_extract_succeeds_when_at_least_one_chunk_completes(
    monkeypatch, tmp_path
):
    """Sanity counter-test: a successful chunk run keeps exit 0. Confirms the
    new guard only fires on the all-failed path, not on every extract."""
    corpus = _make_corpus(tmp_path)
    out_dir = tmp_path / "out"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

    def _one_chunk_succeeded(paths, **kwargs):
        on_chunk = kwargs.get("on_chunk_done")
        if on_chunk:
            on_chunk(0, 1, {"nodes": [], "edges": [], "hyperedges": []})
        return {
            "nodes": [],
            "edges": [],
            "hyperedges": [],
            "input_tokens": 100,
            "output_tokens": 50,
        }

    monkeypatch.setattr(
        "graphify.llm.extract_corpus_parallel", _one_chunk_succeeded
    )
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "extract", str(corpus), "--backend", "claude",
         "--out", str(out_dir)],
    )

    # extract may still raise SystemExit at the end (clean exit code 0)
    # depending on platform; accept either no exception or SystemExit(0).
    try:
        mainmod.main()
    except SystemExit as exc:
        assert exc.code in (None, 0), f"unexpected exit code {exc.code}"

    # graph.json should exist on the happy path
    assert (out_dir / "graphify-out" / "graph.json").exists(), (
        "graph.json must be written on the happy path"
    )


def _code_only_corpus(tmp_path):
    """A corpus with only code — no docs/papers/images."""
    (tmp_path / "auth.py").write_text(
        "def login(user):\n    return validate(user)\n\n"
        "def validate(user):\n    return True\n"
    )
    return tmp_path


def _clear_backend_keys(monkeypatch):
    """Clear every env var that detect_backend() or _get_backend_api_key() reads."""
    for key in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY",
        # bedrock: presence of any of these is treated as a valid credential
        "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID",
        # ollama: a set OLLAMA_BASE_URL triggers backend detection
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_extract_codeonly_succeeds_without_api_key(monkeypatch, tmp_path):
    """A code-only corpus must run with no LLM API key.

    Regression: graphify extract validated a backend upfront and exited 1 with
    'no LLM API key found' even for a code-only corpus that never calls a model.
    The keyless AST path now runs to a written graph.json (#1122).
    """
    corpus = _code_only_corpus(tmp_path)
    out_dir = tmp_path / "out"
    _clear_backend_keys(monkeypatch)
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys, "argv",
        ["graphify", "extract", str(corpus), "--out", str(out_dir)],
    )

    try:
        mainmod.main()
    except SystemExit as exc:
        assert exc.code in (None, 0), f"unexpected exit code {exc.code}"

    graph = out_dir / "graphify-out" / "graph.json"
    assert graph.exists(), "code-only extract must write graph.json without a key"
    import json
    assert len(json.loads(graph.read_text()).get("nodes", [])) > 0


def test_extract_out_keeps_project_root_clean(monkeypatch, tmp_path):
    """`extract --out DIR` routes every artifact to DIR/graphify-out/ and the
    scanned project must not grow a graphify-out/ (or anything else) beside
    its sources.

    Guards the centralized-output workflow: run from the project root with
    --out pointing outside the repo, and the repo stays byte-identical.
    """
    project = tmp_path / "project"
    project.mkdir()
    corpus = _code_only_corpus(project)
    external = tmp_path / "external-graphs"

    _clear_backend_keys(monkeypatch)
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.chdir(corpus)  # run from the project root, like a real user
    monkeypatch.setattr(
        mainmod.sys, "argv",
        ["graphify", "extract", ".", "--out", str(external)],
    )

    try:
        mainmod.main()
    except SystemExit as exc:
        assert exc.code in (None, 0), f"unexpected exit code {exc.code}"

    out = external / "graphify-out"
    assert (out / "graph.json").exists(), "graph.json must land under --out"
    assert (out / "manifest.json").exists(), "manifest.json must land under --out"
    assert not (corpus / "graphify-out").exists(), (
        "scanned project must not grow a graphify-out/ when --out is set"
    )
    assert sorted(p.name for p in corpus.iterdir()) == ["auth.py"], (
        "no stray files may appear in the project root"
    )


def test_extract_without_key_still_errors_when_docs_present(
    monkeypatch, tmp_path, capsys
):
    """Key requirement still fires when semantic work is needed.

    A corpus with a Markdown doc needs LLM semantic extraction, so a keyless
    extract must exit 1 with clear guidance (#1122).
    """
    corpus = _make_corpus(tmp_path)  # includes a Markdown doc
    out_dir = tmp_path / "out"
    _clear_backend_keys(monkeypatch)
    # Patch detect_backend too so ambient AWS/ollama env can't slip through.
    monkeypatch.setattr("graphify.llm.detect_backend", lambda: None)
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys, "argv",
        ["graphify", "extract", str(corpus), "--out", str(out_dir)],
    )

    with pytest.raises(SystemExit) as exc_info:
        mainmod.main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "no LLM API key found" in err
    assert "code-only corpus needs no key" in err
    assert not (out_dir / "graphify-out" / "graph.json").exists()


def test_extract_help_usage_documents_exclude(monkeypatch, capsys):
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(mainmod.sys, "argv", ["graphify", "extract"])

    with pytest.raises(SystemExit) as exc_info:
        mainmod.main()

    assert exc_info.value.code == 1
    assert "--exclude PATTERN" in capsys.readouterr().err


def test_extract_exclude_prunes_semantic_reference_nodes_and_edges(monkeypatch, tmp_path, capsys):
    corpus = tmp_path / "corpus"
    excluded = corpus / ".agents" / "skills" / "task-author"
    excluded.mkdir(parents=True)
    (excluded / "SKILL.md").write_text("# Task Author\n")
    (corpus / "README.md").write_text("# Notes\nReferences task author skill.\n")
    out_dir = tmp_path / "out"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

    def _semantic_with_excluded_reference(paths, **kwargs):
        on_chunk = kwargs.get("on_chunk_done")
        result = {
            "nodes": [
                {"id": "readme", "label": "README", "file_type": "document",
                 "source_file": "README.md"},
                {"id": "task_author_skill", "label": "Task Author Skill",
                 "file_type": "document",
                 "source_file": ".agents/skills/task-author/SKILL.md"},
            ],
            "edges": [
                {"source": "readme", "target": "task_author_skill",
                 "relation": "references", "confidence": "EXTRACTED",
                 "source_file": "README.md", "weight": 1.0},
                {"source": "task_author_skill", "target": "readme",
                 "relation": "references", "confidence": "EXTRACTED",
                 "source_file": ".agents/skills/task-author/SKILL.md", "weight": 1.0},
            ],
            "hyperedges": [],
            "input_tokens": 11,
            "output_tokens": 7,
        }
        if on_chunk:
            on_chunk(0, 1, result)
        return result

    monkeypatch.setattr(
        "graphify.llm.extract_corpus_parallel",
        _semantic_with_excluded_reference,
    )
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        [
            "graphify", "extract", str(corpus), "--backend", "claude",
            "--out", str(out_dir), "--exclude", ".agents/skills",
            "--no-cluster",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        mainmod.main()
    assert exc_info.value.code == 0

    graph_json = out_dir / "graphify-out" / "graph.json"
    graph = json.loads(graph_json.read_text())
    node_sources = {n.get("source_file") for n in graph["nodes"]}
    assert ".agents/skills/task-author/SKILL.md" not in node_sources
    node_ids = {n["id"] for n in graph["nodes"]}
    assert "task_author_skill" not in node_ids
    assert all(
        e.get("source") in node_ids and e.get("target") in node_ids
        for e in graph["edges"]
    )
    assert all(".agents/skills" not in (e.get("source_file") or "") for e in graph["edges"])
    manifest = (out_dir / "graphify-out" / "manifest.json").read_text()
    assert ".agents/skills" not in manifest

    capsys.readouterr()
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "query", "Task Author Skill", "--graph", str(graph_json)],
    )
    mainmod.main()
    assert ".agents/skills" not in capsys.readouterr().out


def test_cluster_only_refused_graph_write_does_not_claim_updated(monkeypatch, tmp_path, capsys):
    project = tmp_path / "project"
    out = project / "graphify-out"
    out.mkdir(parents=True)
    graph = {
        "directed": False,
        "nodes": [
            {"id": "a", "label": "A", "file_type": "concept", "source_file": "a.md", "community": 0},
            {"id": "b", "label": "B", "file_type": "concept", "source_file": "b.md", "community": 1},
        ],
        "links": [],
    }
    (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (out / ".graphify_analysis.json").write_text(
        json.dumps({"tokens": {"input": 1234, "output": 5678}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("graphify.export.to_json", lambda *args, **kwargs: False)
    monkeypatch.setattr("graphify.export._git_head", lambda: None)
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "cluster-only", str(project), "--no-viz", "--no-label"],
    )

    mainmod.main()

    stdout = capsys.readouterr().out
    assert "GRAPH_REPORT.md updated" in stdout
    assert "graph.json skipped" in stdout
    assert "graph.json updated" not in stdout
    report = (out / "GRAPH_REPORT.md").read_text(encoding="utf-8")
    assert "Token cost: 1,234 input · 5,678 output" in report
