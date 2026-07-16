import json

import pytest

from app.retriever import sibling_expansion
from app.retriever.sibling_expansion import _SiblingChunk, _SiblingLeaf, expand_siblings
from app.retriever.strategy import RetrievalKnobs
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _knobs(**updates) -> RetrievalKnobs:
    values = {
        "dense_top_k": 30,
        "sparse_top_k": 10,
        "rerank_top_n": 8,
        "parent_expansion_enabled": True,
        "prefer_operative_enabled": False,
        "retrieval_operative_only": True,
        "consolidated_dedup_enabled": True,
        "sibling_expansion_enabled": True,
        "sibling_expansion_radius": 1,
        "sibling_expansion_max_chars": 100,
        "sibling_expansion_max_tokens": 100,
    }
    values.update(updates)
    return RetrievalKnobs(**values)


def _result(chunk_id: str, label: str, *, parent: str = "p", **metadata):
    return RetrievalResult(
        chunk_id=chunk_id,
        text=chunk_id,
        score=5.0,
        metadata={"parent_key": parent, "unit_label": label, **metadata},
    )


def _leaf(label: str, *parts: str, parent: str = "p", hidden: bool = False):
    chunks = []
    for index, part in enumerate(parts):
        metadata = {"parent_key": parent, "unit_label": label, "source_id": "law"}
        if hidden:
            metadata["operability_action"] = "hide"
        chunks.append(
            _SiblingChunk(
                chunk_id=part,
                chunk_index=index,
                text=part,
                char_count=len(part),
                token_estimate=1,
                metadata=metadata,
            )
        )
    return _SiblingLeaf((parent, label), tuple(chunks))


def _family(monkeypatch, leaves):
    monkeypatch.setattr(sibling_expansion, "_load_families", lambda keys: {"p": leaves})


def test_siblings_are_inserted_in_document_order_with_atomic_split_leaves(monkeypatch):
    _family(
        monkeypatch,
        [_leaf("A", "a1", "a2"), _leaf("B", "b"), _leaf("C", "c1", "c2")],
    )

    output = expand_siblings([_result("b", "B")], knobs=_knobs())

    assert [result.chunk_id for result in output] == ["a1", "a2", "b", "c1", "c2"]
    assert {result.metadata["sibling_offset"] for result in output if result.chunk_id != "b"} == {-1, 1}
    assert all(
        result.metadata["sibling_seed_chunk_id"] == "b"
        for result in output
        if result.chunk_id != "b"
    )


def test_global_budget_is_seed_rank_then_preceding_first_and_leaf_atomic(monkeypatch):
    _family(monkeypatch, [_leaf("A", "aa"), _leaf("B", "b"), _leaf("C", "cc")])

    output = expand_siblings(
        [_result("b", "B")],
        knobs=_knobs(sibling_expansion_max_chars=2),
    )

    assert [result.chunk_id for result in output] == ["aa", "b"]


def test_multi_part_leaf_is_skipped_whole_when_token_budget_is_too_small(monkeypatch):
    _family(monkeypatch, [_leaf("A", "a1", "a2"), _leaf("B", "b")])

    output = expand_siblings(
        [_result("b", "B")],
        knobs=_knobs(sibling_expansion_max_tokens=1),
    )

    assert [result.chunk_id for result in output] == ["b"]


@pytest.mark.parametrize(
    ("seed_id", "seed_label", "expected"),
    (("a", "A", ["a", "b"]), ("c", "C", ["b", "c"])),
)
def test_first_and_last_leaf_boundaries(monkeypatch, seed_id, seed_label, expected):
    _family(monkeypatch, [_leaf("A", "a"), _leaf("B", "b"), _leaf("C", "c")])

    output = expand_siblings([_result(seed_id, seed_label)], knobs=_knobs())

    assert [result.chunk_id for result in output] == expected


def test_radius_two_additions_render_in_document_order(monkeypatch):
    _family(
        monkeypatch,
        [
            _leaf("A", "a"),
            _leaf("B", "b"),
            _leaf("C", "c"),
            _leaf("D", "d"),
            _leaf("E", "e"),
        ],
    )

    output = expand_siblings(
        [_result("c", "C")], knobs=_knobs(sibling_expansion_radius=2)
    )

    assert [result.chunk_id for result in output] == ["a", "b", "c", "d", "e"]


def test_leaf_reachable_from_two_seeds_is_not_readmitted_or_double_budgeted(monkeypatch):
    _family(
        monkeypatch,
        [_leaf("A", "a"), _leaf("B", "b"), _leaf("C", "c"), _leaf("D", "d")],
    )

    output = expand_siblings(
        [_result("b", "B"), _result("d", "D")],
        knobs=_knobs(sibling_expansion_max_chars=3),
    )

    assert [result.chunk_id for result in output] == ["a", "b", "c", "d"]
    assert [result.chunk_id for result in output].count("c") == 1


def test_hidden_leaf_is_excluded_but_missing_operability_is_fail_open(monkeypatch):
    _family(
        monkeypatch,
        [_leaf("A", "a", hidden=True), _leaf("B", "b"), _leaf("C", "c")],
    )

    output = expand_siblings([_result("b", "B")], knobs=_knobs())

    assert [result.chunk_id for result in output] == ["b", "c"]


def test_hidden_leaf_can_expand_when_operative_only_filter_is_disabled(monkeypatch):
    _family(monkeypatch, [_leaf("A", "a", hidden=True), _leaf("B", "b")])

    output = expand_siblings(
        [_result("b", "B")], knobs=_knobs(retrieval_operative_only=False)
    )

    assert [result.chunk_id for result in output] == ["a", "b"]


def test_parent_expanded_and_ineligible_results_are_noops(monkeypatch):
    monkeypatch.setattr(
        sibling_expansion,
        "_load_families",
        lambda keys: pytest.fail("family loading should not run"),
    )
    parent = _result("parent", "Article 1", expanded_from_parent=True)
    plain = RetrievalResult("plain", "plain", 1.0, {})

    assert expand_siblings([parent, plain], knobs=_knobs()) == [parent, plain]
    assert expand_siblings([plain], knobs=_knobs(sibling_expansion_enabled=False)) == [plain]


def test_existing_survivor_leaf_is_not_duplicated_or_repositioned(monkeypatch):
    _family(monkeypatch, [_leaf("A", "a"), _leaf("B", "b"), _leaf("C", "c")])
    ranked = [_result("b", "B"), _result("a", "A")]

    output = expand_siblings(ranked, knobs=_knobs())

    assert [result.chunk_id for result in output] == ["b", "c", "a"]
    assert [result.chunk_id for result in output].count("a") == 1


def test_real_family_loader_groups_split_parts_by_metadata_identity(tmp_path, monkeypatch):
    db_path = tmp_path / "chunks.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE chunks(chunk_id TEXT, chunk_index INTEGER, text TEXT, "
        "char_count INTEGER, token_estimate INTEGER, metadata_json TEXT)"
    )
    for chunk_id, index, label in (("a1", 1, "A"), ("a2", 2, "A"), ("b", 3, "B")):
        conn.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?)",
            (chunk_id, index, chunk_id, len(chunk_id), 1, json.dumps({"parent_key": "p", "unit_label": label})),
        )
    conn.commit()
    conn.close()
    monkeypatch.setattr("app.db.settings.db_path", str(db_path))

    families = sibling_expansion._load_families({"p"})

    assert [[chunk.chunk_id for chunk in leaf.chunks] for leaf in families["p"]] == [
        ["a1", "a2"],
        ["b"],
    ]
