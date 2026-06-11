import hashlib

import pytest

from app.ingestion.hashing import hash_content

pytestmark = pytest.mark.unit


def test_hash_is_stable():
    # Same input always yields the same hash.
    assert hash_content("Article 1156") == hash_content("Article 1156")


def test_hash_differs_for_different_input():
	assert hash_content("Article 1156") != hash_content("Article 1157")


def test_hash_matches_known_sha256_vector():
	# Locks the algorithm: SHA-256 hex of the UTF-8 bytes.
	expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
	assert hash_content("hello world") == expected


def test_hash_matches_hashlib_for_arbitrary_text():
	text = "Republic Act No. 10173"
	assert hash_content(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_hash_handles_non_ascii():
	# UTF-8 encoding must not raise on accented characters.
	assert hash_content("café résumé") == hashlib.sha256(
		"café résumé".encode("utf-8")
	).hexdigest()


def test_hash_of_empty_string():
	expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
	assert hash_content("") == expected
