"""
Tests for app/classification/llm_classifier.py

The HTTP call to the LLM is patched — only JSON parsing, normalization,
error handling, and confidence clamping are tested.
"""
import json
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.classification.llm_classifier import LLMClassifier
from tests.conftest import FakeEmail, FakeSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(payload: dict, status: int = 200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = payload
    mock.text = json.dumps(payload)
    return mock


def _llm_body(
    folder="Invoices",
    confidence=0.95,
    sender_type="company",
    sender_name="Amazon",
):
    return {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "folder": folder,
                    "confidence": confidence,
                    "sender_type": sender_type,
                    "sender_name": sender_name,
                })
            }
        }]
    }


def _patch_post(return_value):
    return patch("httpx.AsyncClient.post", new=AsyncMock(return_value=return_value))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_successful_classification():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(_llm_body())):
        result = await clf.classify(FakeEmail())
    assert result.folder == "Invoices"
    assert result.confidence == 0.95
    assert result.sender_type == "company"
    assert result.sender_name == "Amazon"


async def test_json_embedded_in_prose():
    """LLM sometimes wraps JSON in explanatory text — extractor must still work."""
    content = (
        'Sure! Here is the result: '
        '{"folder": "Spam", "confidence": 0.88, "sender_type": "company", "sender_name": "SpamCo"}'
        ' Hope that helps!'
    )
    payload = {"choices": [{"message": {"content": content}}]}
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(payload)):
        result = await clf.classify(FakeEmail())
    assert result.folder == "Spam"
    assert result.confidence == 0.88


# ---------------------------------------------------------------------------
# Confidence clamping
# ---------------------------------------------------------------------------

async def test_confidence_clamped_above_1():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(_llm_body(confidence=1.5))):
        result = await clf.classify(FakeEmail())
    assert result.confidence == 1.0


async def test_confidence_clamped_below_0():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(_llm_body(confidence=-0.5))):
        result = await clf.classify(FakeEmail())
    assert result.confidence == 0.0


async def test_confidence_string_parsed():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(_llm_body(confidence="0.85"))):
        result = await clf.classify(FakeEmail())
    assert result.confidence == 0.85


# ---------------------------------------------------------------------------
# Sender field normalisation
# ---------------------------------------------------------------------------

async def test_invalid_sender_type_normalised_to_none():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(_llm_body(sender_type="robot"))):
        result = await clf.classify(FakeEmail())
    assert result.sender_type is None


async def test_sender_name_null_string_normalised():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(_llm_body(sender_name="null"))):
        result = await clf.classify(FakeEmail())
    assert result.sender_name is None


async def test_sender_name_empty_string_normalised():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(_llm_body(sender_name=""))):
        result = await clf.classify(FakeEmail())
    assert result.sender_name is None


async def test_sender_name_NULL_uppercase_normalised():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(_llm_body(sender_name="NULL"))):
        result = await clf.classify(FakeEmail())
    assert result.sender_name is None


# ---------------------------------------------------------------------------
# Error handling — all should return NeedsReview, confidence 0.0
# ---------------------------------------------------------------------------

async def test_http_500_returns_needs_review():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response({}, status=500)):
        result = await clf.classify(FakeEmail())
    assert result.folder == "NeedsReview"
    assert result.confidence == 0.0


async def test_malformed_json_content_returns_needs_review():
    payload = {"choices": [{"message": {"content": "not json at all"}}]}
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response(payload)):
        result = await clf.classify(FakeEmail())
    assert result.folder == "NeedsReview"


async def test_empty_choices_returns_needs_review():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response({"choices": []})):
        result = await clf.classify(FakeEmail())
    assert result.folder == "NeedsReview"


async def test_missing_choices_key_returns_needs_review():
    clf = LLMClassifier(FakeSettings())
    with _patch_post(_mock_response({"unexpected": "response"})):
        result = await clf.classify(FakeEmail())
    assert result.folder == "NeedsReview"


async def test_network_error_returns_needs_review():
    clf = LLMClassifier(FakeSettings())
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.RequestError("timeout"))):
        result = await clf.classify(FakeEmail())
    assert result.folder == "NeedsReview"
    assert result.confidence == 0.0
