from typer.testing import CliRunner

from searchbench.cli import app


def test_help_lists_the_tool() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Benchmark web search APIs" in result.output
