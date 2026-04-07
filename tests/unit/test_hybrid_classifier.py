"""
Tests for app/classification/hybrid_classifier.py

All external classifiers are mocked — only the orchestration logic is tested.

v2.0 behaviour: rules are hints, LLM always validates when a rule matches.
  - Rule + LLM agree  → source="rule_confirmed", confidence ≥ 0.95, folder moved
  - Rule + LLM differ → source="rule_conflict",  folder="NeedsReview"
  - No rule           → pure LLM, threshold applies as before
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.classification.hybrid_classifier import HybridClassifier
from app.classification.contracts import ClassificationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule_hit(folder="Invoices"):
    r = ClassificationResult(folder, 1.0)
    r.source = "rule"
    return r


def _llm_hit(folder="Work", confidence=0.9):
    r = ClassificationResult(folder, confidence)
    r.source = "llm"
    r.sender_type = "company"
    r.sender_name = "Acme Corp"
    r.prompt_tokens = 100
    r.completion_tokens = 20
    r.total_tokens = 120
    return r


def _make_clf(rule_result, llm_result, threshold=0.75):
    rule = MagicMock()
    rule.classify = AsyncMock(return_value=rule_result)
    llm = MagicMock()
    llm.classify = AsyncMock(return_value=llm_result)
    return HybridClassifier(rule, llm, threshold=threshold), rule, llm


# ---------------------------------------------------------------------------
# Rule + LLM agree → rule_confirmed
# ---------------------------------------------------------------------------

async def test_rule_confirmed_when_llm_agrees():
    """Rule matches + LLM returns same folder → rule_confirmed, email moved."""
    clf, _, llm = _make_clf(_rule_hit("Invoices"), _llm_hit("Invoices", 0.85))
    result = await clf.classify(MagicMock())
    assert result.folder == "Invoices"
    assert result.source == "rule_confirmed"
    llm.classify.assert_called_once()


async def test_rule_confirmed_confidence_boosted():
    """Confidence is boosted to at least 0.95 on agreement."""
    clf, _, _ = _make_clf(_rule_hit("Invoices"), _llm_hit("Invoices", 0.80))
    result = await clf.classify(MagicMock())
    assert result.confidence >= 0.95


async def test_rule_confirmed_high_llm_confidence_kept():
    """If LLM confidence is already above 0.95, it is preserved."""
    clf, _, _ = _make_clf(_rule_hit("Invoices"), _llm_hit("Invoices", 0.98))
    result = await clf.classify(MagicMock())
    assert result.confidence == 0.98


async def test_rule_confirmed_rule_folder_set():
    clf, _, _ = _make_clf(_rule_hit("Invoices"), _llm_hit("Invoices", 0.9))
    result = await clf.classify(MagicMock())
    assert result.rule_folder == "Invoices"


async def test_llm_always_called_when_rule_matches():
    """LLM is always invoked for validation — never bypassed by a rule."""
    clf, _, llm = _make_clf(_rule_hit("Invoices"), _llm_hit("Invoices", 0.9))
    await clf.classify(MagicMock())
    llm.classify.assert_called_once()


# ---------------------------------------------------------------------------
# Rule + LLM disagree → rule_conflict → NeedsReview
# ---------------------------------------------------------------------------

async def test_rule_conflict_when_llm_disagrees():
    """Rule says Invoices, LLM says Marketing → NeedsReview."""
    clf, _, _ = _make_clf(_rule_hit("Invoices"), _llm_hit("Marketing", 0.88))
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"
    assert result.source == "rule_conflict"


async def test_rule_conflict_stores_both_folders():
    clf, _, _ = _make_clf(_rule_hit("Invoices"), _llm_hit("Marketing", 0.88))
    result = await clf.classify(MagicMock())
    assert result.rule_folder == "Invoices"
    assert result.llm_folder == "Marketing"


async def test_rule_conflict_preserves_llm_confidence():
    clf, _, _ = _make_clf(_rule_hit("Invoices"), _llm_hit("Spam", 0.82))
    result = await clf.classify(MagicMock())
    assert result.confidence == 0.82


async def test_rule_conflict_preserves_sender_identity():
    llm = _llm_hit("Marketing", 0.88)
    llm.sender_type = "company"
    llm.sender_name = "Amazon"
    clf, _, _ = _make_clf(_rule_hit("Invoices"), llm)
    result = await clf.classify(MagicMock())
    assert result.sender_type == "company"
    assert result.sender_name == "Amazon"


# ---------------------------------------------------------------------------
# No rule → pure LLM (unchanged from v1)
# ---------------------------------------------------------------------------

async def test_no_rule_falls_through_to_llm():
    clf, _, llm = _make_clf(None, _llm_hit("Work", 0.9))
    result = await clf.classify(MagicMock())
    assert result.folder == "Work"
    llm.classify.assert_called_once()


async def test_llm_above_threshold_is_accepted():
    clf, _, _ = _make_clf(None, _llm_hit("Spam", 0.76))
    result = await clf.classify(MagicMock())
    assert result.folder == "Spam"


async def test_llm_at_threshold_is_accepted():
    clf, _, _ = _make_clf(None, _llm_hit("Marketing", 0.75))
    result = await clf.classify(MagicMock())
    assert result.folder == "Marketing"


async def test_llm_below_threshold_becomes_needs_review():
    clf, _, _ = _make_clf(None, _llm_hit("Work", 0.74))
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"
    assert result.confidence == 0.74


async def test_llm_zero_confidence_becomes_needs_review():
    clf, _, _ = _make_clf(None, _llm_hit("Work", 0.0))
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"


async def test_custom_threshold_respected():
    clf, _, _ = _make_clf(None, _llm_hit("Work", 0.85), threshold=0.90)
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"


async def test_needs_review_preserves_original_confidence():
    clf, _, _ = _make_clf(None, _llm_hit("Work", 0.60))
    result = await clf.classify(MagicMock())
    assert result.confidence == 0.60
