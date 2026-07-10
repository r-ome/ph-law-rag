import logging

import pytest
import structlog
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _restore_logging():
    """The CLI callback configures structlog globally; undo it so later
    capsys-based tests still see unconfigured-structlog stdout output."""
    yield
    from app.observability import logger as obs

    obs._remove_owned_handlers(logging.getLogger("raglab"))
    obs._configured = False
    structlog.reset_defaults()


def test_eval_holdout_requires_explicit_release_flag():
    from app.cli.main import app

    result = CliRunner().invoke(app, ["eval", "--split", "holdout", "--no-score"])

    assert result.exit_code != 0
    assert "holdout is release-only" in result.output
