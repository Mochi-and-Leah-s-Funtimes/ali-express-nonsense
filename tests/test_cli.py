import json

import pytest
from click.testing import CliRunner

from ali import cli


def _runner(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("ALI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALI_DELAY", "0")
    return runner


def test_budget_set_and_status(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    result = runner.invoke(cli.main, ["budget", "--set", "150"])
    assert result.exit_code == 0
    assert "150" in result.output

    result = runner.invoke(cli.main, ["budget", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["cap"] == 150.0
    assert payload["spent"] == 0.0
    assert payload["remaining"] == 150.0


def test_spend_add_and_budget(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli.main, ["budget", "--set", "100"])

    result = runner.invoke(
        cli.main,
        [
            "spend",
            "add",
            "1005009066568188",
            "--price",
            "4.46",
            "--qty",
            "2",
            "--shipping",
            "1.54",
            "--title",
            "Hub",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli.main, ["budget", "--json"])
    payload = json.loads(result.output)
    assert payload["spent"] == 10.46
    assert payload["remaining"] == pytest.approx(100 - 10.46)


def test_spend_remove(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli.main, ["spend", "add", "1", "--price", "5"])
    result = runner.invoke(cli.main, ["spend", "list", "--json"])
    entry_id = json.loads(result.output)["entries"][0]["id"]
    result = runner.invoke(cli.main, ["spend", "remove", str(entry_id)])
    assert result.exit_code == 0
    assert "removed" in result.output


def test_watch_lifecycle(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    result = runner.invoke(cli.main, ["watch", "add", "1005009066568188", "--title", "Hub"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli.main, ["watch", "list", "--json"])
    entries = json.loads(result.output)
    assert len(entries) == 1
    assert entries[0]["title"] == "Hub"

    result = runner.invoke(cli.main, ["watch", "remove", "1005009066568188"])
    assert "removed" in result.output


def test_config_output(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    result = runner.invoke(cli.main, ["config", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["currency"] == "USD"
    assert payload["monthly_budget"] == 150.0


def test_help_lists_commands(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    for command in ("search", "product", "budget", "spend", "watch", "plan"):
        assert command in result.output
