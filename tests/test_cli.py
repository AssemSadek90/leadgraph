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
