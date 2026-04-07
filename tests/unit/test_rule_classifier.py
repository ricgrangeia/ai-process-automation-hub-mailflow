"""
Tests for app/classification/rule_classifier.py

Two sections:
  - Hardcoded rules: no DB needed (session_factory=None)
  - Learned rules: DB session is mocked
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.classification.rule_classifier import RuleClassifier
from tests.conftest import FakeEmail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_factory(rules: list):
    """
    Returns an async context-manager session factory that yields a mock session
    which returns the given LearnedRule objects on execute().
    The factory is called multiple times (select + hit_count update), so each
    call returns a fresh context manager backed by the same session mock.
    """
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = rules

    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    # session.get() returns a copy of the matched rule (for hit_count increment)
    async def _fake_get(model, pk):
        return next((r for r in rules if r.id == pk), None)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.get = _fake_get
    mock_session.commit = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)

    session_factory = MagicMock(return_value=cm)
    return session_factory


def _make_rule(
    id=1,
    tenant_id=1,
    match_field="sender_domain",
    match_value="amazon.com",
    folder="Invoices",
):
    rule = MagicMock()
    rule.id = id
    rule.active = True
    rule.tenant_id = tenant_id
    rule.match_field = match_field
    rule.match_value = match_value
    rule.actions = [{"type": "move_folder", "folder": folder}]
    rule.hit_count = 0
    return rule


# ---------------------------------------------------------------------------
# Hardcoded rules (no DB)
# ---------------------------------------------------------------------------

async def test_invoice_keyword_subject():
    clf = RuleClassifier()
    result = await clf.classify(FakeEmail(subject="invoice #123", body_text=""))
    assert result is not None
    assert result.folder == "Invoices"
    assert result.confidence == 1.0


async def test_fatura_keyword_body():
    clf = RuleClassifier()
    result = await clf.classify(FakeEmail(subject="Pagamento", body_text="Segue a fatura em anexo."))
    assert result is not None
    assert result.folder == "Invoices"


async def test_unsubscribe_body():
    clf = RuleClassifier()
    result = await clf.classify(FakeEmail(subject="Newsletter", body_text="Click here to unsubscribe from our list."))
    assert result is not None
    assert result.folder == "Marketing"


async def test_no_match_returns_none():
    clf = RuleClassifier()
    result = await clf.classify(FakeEmail(subject="Hello", body_text="How are you doing today?"))
    assert result is None


async def test_case_insensitive_invoice():
    clf = RuleClassifier()
    result = await clf.classify(FakeEmail(subject="INVOICE #999", body_text=""))
    assert result is not None
    assert result.folder == "Invoices"


# ---------------------------------------------------------------------------
# Learned rules (mocked DB)
# ---------------------------------------------------------------------------

async def test_learned_sender_domain_matches():
    rule = _make_rule(match_field="sender_domain", match_value="amazon.com", folder="Invoices")
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    result = await clf.classify(FakeEmail(from_address="orders@amazon.com", subject="Hi", body_text="Hi"))
    assert result is not None
    assert result.folder == "Invoices"
    assert result.confidence == 1.0


async def test_learned_sender_domain_no_match():
    rule = _make_rule(match_field="sender_domain", match_value="amazon.com", folder="Invoices")
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    result = await clf.classify(FakeEmail(from_address="orders@ebay.com", subject="Hi", body_text="Hi"))
    # ebay.com doesn't match amazon.com; no hardcoded rule fires either
    assert result is None


async def test_learned_sender_email_exact():
    rule = _make_rule(match_field="sender_email", match_value="boss@company.com", folder="Work")
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    result = await clf.classify(FakeEmail(from_address="boss@company.com", subject="Hi", body_text="Hi"))
    assert result.folder == "Work"


async def test_learned_subject_contains():
    rule = _make_rule(match_field="subject_contains", match_value="recibo", folder="Invoices")
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    result = await clf.classify(FakeEmail(subject="Recibo de Pagamento #456", body_text="Hi"))
    assert result.folder == "Invoices"


async def test_learned_body_contains():
    rule = _make_rule(match_field="body_contains", match_value="purchase order", folder="Work")
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    result = await clf.classify(FakeEmail(subject="Hi", body_text="Please see the attached purchase order."))
    assert result.folder == "Work"


async def test_learned_rule_case_insensitive():
    rule = _make_rule(match_field="sender_domain", match_value="amazon.com", folder="Invoices")
    clf = RuleClassifier(session_factory=_make_session_factory([rule]))
    result = await clf.classify(FakeEmail(from_address="orders@AMAZON.COM", subject="Hi", body_text="Hi"))
    assert result.folder == "Invoices"


async def test_no_learned_rules_falls_through():
    clf = RuleClassifier(session_factory=_make_session_factory([]))
    result = await clf.classify(FakeEmail(subject="Hi", body_text="Hi"))
    assert result is None
