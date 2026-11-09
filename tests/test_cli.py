from typer.testing import CliRunner

from leadgraph import cli


def test_approve_interactively(monkeypatch):
    answers = iter(["1,3", "https://extra.example/list"])
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: next(answers))
    value = {"candidates": [{"url": f"https://s{i}.de", "source_type": "other",
                             "estimated_count": 10, "notes": ""} for i in range(3)]}
    assert cli.approve_interactively(value) == {
        "approved": [0, 2], "extra": ["https://extra.example/list"]}


def test_missing_keys_exit_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("LEADGRAPH_OUTPUTS_DIR", str(tmp_path))
    result = CliRunner().invoke(cli.app, ["run", "--city", "berlin", "--area", "mitte"])
    assert result.exit_code == 2 and "ANTHROPIC_API_KEY" in result.output


def test_resume_unknown_run(tmp_path):
    result = CliRunner().invoke(cli.app, ["resume", "does-not-exist"])
    assert result.exit_code == 2 and "No run.json" in result.output


def test_sample_and_accuracy_commands(tmp_path, monkeypatch):
    import json

    from leadgraph import config
    run = tmp_path / "r1"
    run.mkdir()
    comps = [{"id": f"c{i}", "name": f"C{i}", "tier": t, "is_property_manager": True,
              "kpis": None, "contacts": []} for i, t in enumerate("AABBC")]
    (run / "companies.jsonl").write_text("\n".join(json.dumps(c) for c in comps))
    (run / "report.json").write_text(json.dumps({"coverage": {"coverage": 0.74,
                                                              "chapman_estimate": 1348.26}}))
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path)
    gold = tmp_path / "gold.csv"
    r = CliRunner().invoke(cli.app, ["sample", "r1", "--per-tier", "1", "--out", str(gold)])
    assert r.exit_code == 0 and gold.exists()
    again = CliRunner().invoke(cli.app, ["sample", "r1", "--out", str(gold)])
    assert again.exit_code == 2  # never overwrites labels without --force
    from leadgraph.validation_sample import read_gold, write_gold
    rows = read_gold(gold)
    assert len(rows) == 3
    rows[0].update(true_is_property_manager="y", true_units_managed="n/a")
    write_gold(gold, rows)
    r = CliRunner().invoke(cli.app, ["accuracy", "r1", "--gold", str(gold)])
    assert r.exit_code == 0, r.output
    result = json.loads((run / "validation.json").read_text())
    assert result["classification"]["n"] == 1 and result["chapman"]["coverage"] == 0.74


def test_approval_prompt_without_input_pauses(monkeypatch):
    import pytest

    from leadgraph.runner import NeedsApproval

    def eof(*a, **k):
        raise EOFError

    monkeypatch.setattr(cli.typer, "prompt", eof)
    with pytest.raises(NeedsApproval):
        cli.approve_interactively({"candidates": [{"url": "https://s.de", "source_type": "other",
                                                   "estimated_count": 1, "notes": ""}]})
