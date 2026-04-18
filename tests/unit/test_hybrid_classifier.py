"""
Tests for app/classification/hybrid_classifier.py

Architecture: rules provide context, LLM always decides.

  rule.get_context(email) → dict of sender history + matched keywords
  llm.classify(email, context=...) → ClassificationResult
  confidence >= threshold → return result as-is
  confidence <  threshold → NeedsReview (with original metadata copied)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.classification.hybrid_classifier import HybridClassifier
from app.classification.contracts import ClassificationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm_hit(folder="Work", confidence=0.9):
    r = ClassificationResult(folder, confidence)
    r.source = "llm"
    r.sender_type = "company"
    r.sender_name = "Acme Corp"
    r.prompt_tokens = 100
    r.completion_tokens = 20
    r.total_tokens = 120
    r.llm_time_seconds = 0.5
    return r


def _make_clf(llm_result, context=None, threshold=0.75):
    rule = MagicMock()
    rule.get_context = AsyncMock(return_value=context or {})
    llm = MagicMock()
    llm.classify = AsyncMock(return_value=llm_result)
    return HybridClassifier(rule, llm, threshold=threshold), rule, llm


# ---------------------------------------------------------------------------
# LLM above / at / below threshold
# ---------------------------------------------------------------------------

async def test_llm_above_threshold_folder_returned():
    clf, _, _ = _make_clf(_llm_hit("Work", 0.9))
    result = await clf.classify(MagicMock())
    assert result.folder == "Work"


async def test_llm_at_threshold_is_accepted():
    clf, _, _ = _make_clf(_llm_hit("Marketing", 0.75))
    result = await clf.classify(MagicMock())
    assert result.folder == "Marketing"


async def test_llm_above_threshold_confidence_preserved():
    clf, _, _ = _make_clf(_llm_hit("Spam", 0.88))
    result = await clf.classify(MagicMock())
    assert result.confidence == 0.88


async def test_llm_below_threshold_becomes_needs_review():
    clf, _, _ = _make_clf(_llm_hit("Work", 0.74))
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"


async def test_llm_zero_confidence_becomes_needs_review():
    clf, _, _ = _make_clf(_llm_hit("Work", 0.0))
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"


async def test_needs_review_preserves_original_confidence():
    clf, _, _ = _make_clf(_llm_hit("Work", 0.60))
    result = await clf.classify(MagicMock())
    assert result.confidence == 0.60


async def test_custom_threshold_respected():
    clf, _, _ = _make_clf(_llm_hit("Work", 0.85), threshold=0.90)
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"


async def test_custom_threshold_high_confidence_passes():
    clf, _, _ = _make_clf(_llm_hit("Work", 0.95), threshold=0.90)
    result = await clf.classify(MagicMock())
    assert result.folder == "Work"


# ---------------------------------------------------------------------------
# Rule context wiring
# ---------------------------------------------------------------------------

async def test_get_context_always_called():
    """rule.get_context must be called on every classify call."""
    clf, rule, _ = _make_clf(_llm_hit("Work", 0.9))
    email = MagicMock()
    await clf.classify(email)
    rule.get_context.assert_called_once_with(email)


async def test_llm_receives_context_from_rule():
    """The dict returned by get_context is forwarded to llm.classify."""
    ctx = {"sender_history": [{"folder": "Invoices", "hits": 5}], "matched_keywords": []}
    clf, _, llm = _make_clf(_llm_hit("Invoices", 0.95), context=ctx)
    email = MagicMock()
    await clf.classify(email)
    _, kwargs = llm.classify.call_args
    assert kwargs.get("context") == ctx


async def test_llm_always_called():
    clf, _, llm = _make_clf(_llm_hit("Work", 0.9))
    await clf.classify(MagicMock())
    llm.classify.assert_called_once()


# ---------------------------------------------------------------------------
# NeedsReview metadata preservation
# ---------------------------------------------------------------------------

async def test_needs_review_preserves_sender_identity():
    llm_result = _llm_hit("Work", 0.5)
    llm_result.sender_type = "company"
    llm_result.sender_name = "Amazon"
    clf, _, _ = _make_clf(llm_result)
    result = await clf.classify(MagicMock())
    assert result.folder == "NeedsReview"
    assert result.sender_type == "company"
    assert result.sender_name == "Amazon"


async def test_needs_review_preserves_token_counts():
    llm_result = _llm_hit("Work", 0.4)
    llm_result.prompt_tokens = 200
    llm_result.completion_tokens = 50
    llm_result.total_tokens = 250
    clf, _, _ = _make_clf(llm_result)
    result = await clf.classify(MagicMock())
    assert result.prompt_tokens == 200
    assert result.completion_tokens == 50
    assert result.total_tokens == 250


async def test_needs_review_preserves_source():
    llm_result = _llm_hit("Work", 0.3)
    llm_result.source = "llm"
    clf, _, _ = _make_clf(llm_result)
    result = await clf.classify(MagicMock())
    assert result.source == "llm"
