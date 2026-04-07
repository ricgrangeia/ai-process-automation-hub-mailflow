"""
Tests for app/classification/hybrid_classifier.py

All external classifiers are mocked — only the orchestration logic is tested.
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
# Tests
# ---------------------------------------------------------------------------

async def test_rule_match_returns_immediately():
    clf, rule, llm = _make_clf(_rule_hit("Invoices"), _llm_hit())
    result = await clf.classify(MagicMock())
    assert result.folder == "Invoices"
    llm.classify.assert_not_called()


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


async def test_rule_takes_priority_over_high_confidence_llm():
    """Even a 1.0-confidence LLM result is ignored when a rule fires."""
    clf, _, llm = _make_clf(_rule_hit("Invoices"), _llm_hit("Work", 1.0))
    result = await clf.classify(MagicMock())
    assert result.folder == "Invoices"
    llm.classify.assert_not_called()


async def test_custom_threshold_respected():
    clf, _, _ = _make_clf(None, _llm_hit("Work", 0.85), threshold=0.90)
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"


async def test_needs_review_preserves_original_confidence():
    clf, _, _ = _make_clf(None, _llm_hit("Work", 0.60))
    result = await clf.classify(MagicMock())
    assert result.confidence == 0.60
