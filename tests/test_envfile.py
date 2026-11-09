import os

from leadgraph.envfile import load_env_file, parse_env


def test_parse_env():
    text = '# keys\nA=1\nexport B="two words"\nC=\'x=y\'\n\nbad line\nD = spaced \n'
    assert parse_env(text) == {"A": "1", "B": "two words", "C": "x=y", "D": "spaced"}


def test_load_env_file_keeps_existing(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("LG_TEST_A=from_file\nLG_TEST_B=from_file\n", encoding="utf-8")
    monkeypatch.setenv("LG_TEST_A", "from_env")
    monkeypatch.delenv("LG_TEST_B", raising=False)
    assert load_env_file(p) == ["LG_TEST_B"]
    assert os.environ["LG_TEST_A"] == "from_env" and os.environ["LG_TEST_B"] == "from_file"
    monkeypatch.delenv("LG_TEST_B")
    assert load_env_file(tmp_path / "missing.env") == []
