import pytest

from app.retriever.prompts import (
    ABSTAIN_MESSAGE,
    ABSTAIN_PREFIX,
    is_abstention
)

def test_exact_abstain_message_is_refusal():
    assert is_abstention(ABSTAIN_MESSAGE) is True

def test_prefix_alone_is_refusal():
    assert is_abstention(ABSTAIN_PREFIX) is True
    
def test_substantive_answer_without_phrase_is_not_refusal():
    answer = "Under article 1318, consent, object and cause are required.[1]"
    assert is_abstention(answer) is False
    
def test_answer_by_boilerplate_is_not_refusal():
    answer = (
        "The requisites of a valid contract are consent, object and cause "
        "under Article 1318, [1] Beyond that " + ABSTAIN_PREFIX + " to answer fully."
    )
    assert is_abstention(answer) is False
    
def test_boilerplate_then_explanation_is_refusal():
    answer = (
        ABSTAIN_PREFIX + " The indexed corpus does not cover maritime law."
    )
    assert is_abstention(answer) is True
    
def test_no_phrase_is_not_refusal():
    assert is_abstention("Article 1156 defines an obligation. [1]") is False
    
def test_empty_string_is_not_refusal():
    assert is_abstention("") is False
    
def test_short_preamble_below_threshold_is_refusal():
    preamble = "x" * 39 + ABSTAIN_PREFIX
    print(len(preamble))
    assert is_abstention(preamble) is True
    
def test_preamble_at_threshold_is_not_refusal():
    preamble = "x" * 40 + ABSTAIN_PREFIX
    assert is_abstention(preamble) is False
    
def test_whitespace_preamble_is_refusal():
    answer = " \n\t " + ABSTAIN_PREFIX + " to answer that question."
    assert is_abstention(answer) is True