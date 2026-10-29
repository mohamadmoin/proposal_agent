"""The command line: config checks never need keys, and a fake run writes the three output files."""

import json

import pytest

from engine import cli

KEYS = ("GOOGLE_API_KEY", "AGENT_GOOGLE_API_KEY", "GEMINI_TOKEN", "OPENAI_API_KEY", "LLM_API_KEY")
TITLE = "شیوع کمبود ویتامین D در زنان باردار مراجعه‌کننده به مراکز بهداشتی تهران"


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for k in KEYS + ("PROPOSAL_ENGINE", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda path: [])


def test_check_without_keys_says_what_is_missing(capsys):
    assert cli.main(["check"]) == 2
    out = capsys.readouterr().out
    assert "engine: agentic" in out and "GOOGLE_API_KEY" in out


def test_check_never_prints_a_key(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-value-123")
    assert cli.main(["check"]) == 0
    assert "secret-value-123" not in capsys.readouterr().out


def test_agentic_run_without_keys_stops_before_any_work(capsys):
    assert cli.main(["run", TITLE]) == 2
    assert "needs a Gemini key" in capsys.readouterr().err


def test_an_openai_key_alone_is_not_enough_for_the_agentic_engine(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert cli.config_problem("agentic")


def test_fake_run_writes_docx_result_and_trace(tmp_path, capsys):
    out = tmp_path / "run"
    assert cli.main(["run", TITLE, "--fake", "--quiet", "--out", str(out)]) == 0
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    trace = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    assert result["status"] == "done"
    assert result["references"], "the fake run cites the saved PubMed records"
    assert trace["engine"] == "pipeline" and trace["steps"]
    assert all("messages" not in call for call in trace["calls"]), "prompts stay out of the trace file"
    assert (out / "proposal.docx").read_bytes()[:2] == b"PK"
    assert "draft" in capsys.readouterr().out


def test_dotenv_does_not_override_the_shell(tmp_path, monkeypatch):
    monkeypatch.undo()  # use the real loader
    env = tmp_path / ".env"
    env.write_text("PROPOSAL_ENGINE=pipeline\nNEW_VAR_FOR_TEST='x'\n# comment\n", encoding="utf-8")
    monkeypatch.setenv("PROPOSAL_ENGINE", "agentic")
    monkeypatch.delenv("NEW_VAR_FOR_TEST", raising=False)
    read = cli.load_dotenv(env)
    import os

    assert os.environ["PROPOSAL_ENGINE"] == "agentic" and read == ["NEW_VAR_FOR_TEST"]
    assert os.environ["NEW_VAR_FOR_TEST"] == "x"
    monkeypatch.delenv("NEW_VAR_FOR_TEST")
