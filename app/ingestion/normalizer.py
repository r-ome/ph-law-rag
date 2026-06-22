import re

def normalize_text(text: str) -> str:
	"""Normalize fetched text for hashing/chunking. Deterministic and idempotent.

	DELIBERATE DIVERGENCE from the Milestone 2 spec (pinned — do not "fix" casually).
	The spec said "collapse intra-line whitespace" and "collapse 3+ blank lines to 2";
	this implementation instead (a) replaces each whitespace char 1:1, so intra-line
	runs are preserved, and (b) collapses any run of blank lines to a single blank
	line. This is the behaviour the whole corpus was hashed and indexed under, so
	changing it changes every content_hash and forces a FULL re-index — including the
	cloud Qdrant collection through the Bedrock rate limit. Treat current behaviour as
	the spec; see tests/unit/test_normalizer.py (test_DIVERGENCE_*).
	"""
	lines = text.splitlines()
	normalized_lines = []
	previous_blank = False

	for line in lines:
		cleaned = re.sub(r"\s", " ", line).strip()
		if cleaned == "":
			if previous_blank:
				continue
			previous_blank = True
			normalized_lines.append("")
			continue

		previous_blank = False
		normalized_lines.append(cleaned)

	result = "\n".join(normalized_lines).strip()
	return result
