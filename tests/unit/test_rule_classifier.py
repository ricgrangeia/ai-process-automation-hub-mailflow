"""
Tests for app/classification/rule_classifier.py

RuleClassifier.get_context(email) returns a dict:
  {
      "sender_history": [{"folder": "Faturas", "hits": 15}, ...],  # sorted desc
      "matched_keywords": ["fatura", "pagamento"],
  }

This context is injected into the LLM prompt — the LLM always makes the
final classification decision.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.classification.rule_classifier import RuleClassifier
from tests.conftest import FakeEmail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_factory(rules: list):
    """Async context-manager session factory that returns given rules on execute()."""
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = rules

    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=cm)


def _make_rule(
    id=1,
    tenant_id=1,
    sender_email="invoices@amazon.com",
    folder="Invoices",
    hit_count=0,
):
    """Rule with conditions-list format containing a sender_email condition."""
    rule = MagicMock()
    rule.id = id
    rule.active = True
    rule.tenant_id = tenant_id
    rule.hit_count = hit_count
    rule.conditions = [{"type": "sender_email", "value": sender_email}]
    rule.actions = [{"type": "move_folder", "folder": folder}]
    return rule


# ---------------------------------------------------------------------------
# No session factory
# ---------------------------------------------------------------------------

async def test_no_session_factory_returns_empty_context():
    clf = RuleClassifier()
    ctx = await clf.get_context(FakeEmail())
    assert ctx == {"sender_history": [], "matched_keywords": []}


# ---------------------------------------------------------------------------
# Sender history from learned rules
# ---------------------------------------------------------------------------

async def test_matching_sender_email_populates_history():
    rule = _make_rule(sender_email="orders@amazon.com", folder="Invoices", hit_count=10)
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    ctx = await clf.get_context(FakeEmail(from_address="orders@amazon.com"))
    assert ctx["sender_history"] == [{"folder": "Invoices", "hits": 10}]


async def test_non_matching_sender_returns_empty_history():
    rule = _make_rule(sender_email="orders@amazon.com", folder="Invoices", hit_count=5)
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    ctx = await clf.get_context(FakeEmail(from_address="other@ebay.com"))
    assert ctx["sender_history"] == []


async def test_sender_email_match_is_case_insensitive():
    rule = _make_rule(sender_email="orders@amazon.com", folder="Invoices", hit_count=3)
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    ctx = await clf.get_context(FakeEmail(from_address="ORDERS@AMAZON.COM"))
    assert ctx["sender_history"] == [{"folder": "Invoices", "hits": 3}]


async def test_no_rules_returns_empty_history():
    clf = RuleClassifier(session_factory=_make_session_factory([]))
    ctx = await clf.get_context(FakeEmail(from_address="someone@example.com"))
    assert ctx["sender_history"] == []


async def test_legacy_rule_conditions_none_is_ignored():
    """A rule with conditions=None (legacy format) does not appear in history."""
    rule = MagicMock()
    rule.active = True
    rule.tenant_id = 1
    rule.hit_count = 5
    rule.conditions = None      # legacy — get_context uses `rule.conditions or []`
    rule.actions = [{"type": "move_folder", "folder": "Invoices"}]
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    ctx = await clf.get_context(FakeEmail(from_address="sender@amazon.com"))
    assert ctx["sender_history"] == []


# ---------------------------------------------------------------------------
# Hit count aggregation and sorting
# ---------------------------------------------------------------------------

async def test_multiple_rules_same_folder_hit_counts_summed():
    rules = [
        _make_rule(id=1, sender_email="boss@company.com", folder="Work", hit_count=4),
        _make_rule(id=2, sender_email="boss@company.com", folder="Work", hit_count=6),
    ]
    clf = RuleClassifier(session_factory=_make_session_factory(rules))
    ctx = await clf.get_context(FakeEmail(from_address="boss@company.com"))
    assert ctx["sender_history"] == [{"folder": "Work", "hits": 10}]


async def test_history_sorted_by_hits_descending():
    rules = [
        _make_rule(id=1, sender_email="boss@company.com", folder="Work", hit_count=2),
        _make_rule(id=2, sender_email="boss@company.com", folder="Invoices", hit_count=15),
        _make_rule(id=3, sender_email="boss@company.com", folder="Marketing", hit_count=7),
    ]
    clf = RuleClassifier(session_factory=_make_session_factory(rules))
    ctx = await clf.get_context(FakeEmail(from_address="boss@company.com"))
    folders = [e["folder"] for e in ctx["sender_history"]]
    assert folders == ["Invoices", "Marketing", "Work"]


async def test_multiple_senders_only_matching_in_history():
    rules = [
        _make_rule(id=1, sender_email="alice@corp.com", folder="Work", hit_count=5),
        _make_rule(id=2, sender_email="bob@spam.com",   folder="Spam", hit_count=8),
    ]
    clf = RuleClassifier(session_factory=_make_session_factory(rules))
    ctx = await clf.get_context(FakeEmail(from_address="alice@corp.com"))
    assert ctx["sender_history"] == [{"folder": "Work", "hits": 5}]


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

async def test_broken_session_factory_returns_empty_context():
    """If the DB call raises, get_context must return empty context without raising."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=Exception("DB down"))
    cm.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=cm)
    clf = RuleClassifier(session_factory=session_factory)
    ctx = await clf.get_context(FakeEmail())
    assert ctx == {"sender_history": [], "matched_keywords": []}
