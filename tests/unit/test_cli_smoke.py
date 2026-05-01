"""Phase 0 smoke tests — verify the CLI loads and lists all subcommands."""

from __future__ import annotations

from typer.testing import CliRunner

from polybot.cli import app

runner = CliRunner()


def test_help_lists_all_subcommands() -> None:
    """`polybot --help` must list every subcommand from the §7 acceptance plan."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    expected = [
        "run",
        "simulate",
        "backtest",
        "chart",
        "setup",
        "ticker",
        "live-orderbook",
        "smoke",
        "health",
        "version",
    ]
    for cmd in expected:
        assert cmd in result.output, f"missing subcommand in help: {cmd}\n{result.output}"


def test_version_subcommand() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "polybot" in result.output


def test_each_subcommand_has_help() -> None:
    """Every subcommand should answer --help without crashing."""
    for cmd in ["run", "simulate", "backtest", "chart", "setup", "ticker", "live-orderbook", "smoke", "health", "version"]:
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"
