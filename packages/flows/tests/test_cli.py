from typer.testing import CliRunner

from flows.cli import app

runner = CliRunner()


def test_cli_run_default_param() -> None:
    """Validate the run command with its default argument."""
    result = runner.invoke(app)
    assert result.exit_code == 0
    assert "Hello system" in result.output
