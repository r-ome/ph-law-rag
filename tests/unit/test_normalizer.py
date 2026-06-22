import pytest

from app.ingestion.normalizer import normalize_text

pytestmark = pytest.mark.unit


def test_strips_leading_and_trailing_whitespace():
	assert normalize_text("   hello   ") == "hello"


def test_converts_tabs_to_spaces():
	assert normalize_text("a\tb") == "a b"


def test_strips_surrounding_blank_lines():
	assert normalize_text("\n\nhello\n\n") == "hello"


def test_collapses_consecutive_blank_lines_to_one():
	# Multiple blank lines between paragraphs collapse to a single blank line.
	assert normalize_text("a\n\n\n\nb") == "a\n\nb"


def test_is_idempotent():
	messy = "  Article 1156\t\tObligations\n\n\n\nare a juridical necessity.  "
	once = normalize_text(messy)
	assert normalize_text(once) == once


def test_empty_input_returns_empty_string():
	assert normalize_text("") == ""
	assert normalize_text("   \n\t  ") == ""


# --- Divergences from the Milestone 2 spec (intended behaviour, pinned) ---
# These tests pin the CURRENT behaviour, which is treated as the spec. Changing
# normalize_text changes every content_hash and forces a FULL re-index (incl. the
# cloud Qdrant collection via the Bedrock rate limit) — so only change it
# deliberately, with a re-index. See normalizer.py for the rationale.

def test_DIVERGENCE_does_not_collapse_intra_line_whitespace_runs():
	# Spec says "collapse intra-line whitespace", but the implementation
	# replaces each whitespace char 1:1 with a space, leaving runs intact.
	# Current behavior: two tabs -> two spaces (NOT collapsed to one).
	assert normalize_text("a\t\tb") == "a  b"


def test_DIVERGENCE_collapses_blank_runs_to_one_not_two():
	# Spec says "collapse 3+ blank lines to 2", but the implementation
	# collapses any run of blank lines down to a single blank line.
	assert normalize_text("a\n\n\n\n\nb") == "a\n\nb"
