"""
Tests for _auto_save_rule() in app/processing/worker.py

Covers:
  - Missing / None from_address → skip without touching DB
  - No inbox-filter keywords match → skip without touching DB
  - Exact sender_email already covered → no duplicate created
  - Different address from same domain → new rule IS created (email-scoped, not domain)
  - New rule saved with correct sender_email + keyword conditions (min_match=2)
  - Email address lowercased before saving
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.processing.worker import _auto_save_rule
from tests.conftest import FakeEmail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_factory(existing_rules: list | None = None):
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = existing_rules or []

    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_mock

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=execute_result)
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)

    session_factory = MagicMock(return_value=cm)
    session_factory._mock_session = mock_session
    return session_factory


def _rule_with_email(address: str):
    rule = MagicMock()
    rule.conditions = [{"type": "sender_email", "value": address}]
    return rule


def _fake_settings(db_url: str = "postgresql://test/db"):
    s = MagicMock()
    s.database_url = db_url
    return s


def _patch_keywords(keywords: list[str]):
    """Patch get_inbox_keywords to return the given list."""
    return patch(
        "app.processing.worker.get_inbox_keywords" if False else
        "app.core.system_settings.get_inbox_keywords",
        return_value=keywords,
    )


# ---------------------------------------------------------------------------
# Missing / None from_address — must skip without touching the DB
# ---------------------------------------------------------------------------

async def test_none_from_address_skipped():
    sf = _make_session_factory()
    email = FakeEmail(from_address=None)
    with patch("app.core.system_settings.get_inbox_keywords", return_value=["fatura"]):
        await _auto_save_rule(sf, email, "Work", 0.95, _fake_settings())
    sf._mock_session.execute.assert_not_called()
    sf._mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# No inbox filter keywords match — must skip without touching the DB
# ---------------------------------------------------------------------------

async def test_no_keywords_skipped():
    sf = _make_session_factory()
    email = FakeEmail(from_address="billing@acme.com", subject="Hello world", body_text="Nothing here")
    with patch("app.core.system_settings.get_inbox_keywords", return_value=["fatura", "invoice"]):
        await _auto_save_rule(sf, email, "Invoices", 0.95, _fake_settings())
    sf._mock_session.execute.assert_not_called()
    sf._mock_session.add.assert_not_called()


async def test_no_settings_skipped():
    """When settings=None, no keyword lookup is possible — must skip."""
    sf = _make_session_factory()
    email = FakeEmail(from_address="billing@acme.com")
    await _auto_save_rule(sf, email, "Invoices", 0.95, settings=None)
    sf._mock_session.execute.assert_not_called()
    sf._mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# Exact sender already covered — must not create a duplicate rule
# ---------------------------------------------------------------------------

async def test_existing_sender_rule_not_duplicated():
    existing = _rule_with_email("orders@amazon.com")
    sf = _make_session_factory(existing_rules=[existing])
    email = FakeEmail(from_address="orders@amazon.com", subject="fatura amazon", body_text="fatura")
    with patch("app.core.system_settings.get_inbox_keywords", return_value=["fatura"]):
        await _auto_save_rule(sf, email, "Invoices", 0.95, _fake_settings())
    sf._mock_session.add.assert_not_called()
    sf._mock_session.commit.assert_not_called()


async def test_different_address_same_domain_creates_new_rule():
    """Rules are email-scoped: orders@ does not block invoices@ from same domain."""
    existing = _rule_with_email("orders@amazon.com")
    sf = _make_session_factory(existing_rules=[existing])
    email = FakeEmail(from_address="invoices@amazon.com", subject="invoice amazon", body_text="invoice")
    with patch("app.core.system_settings.get_inbox_keywords", return_value=["invoice"]):
        await _auto_save_rule(sf, email, "Invoices", 0.95, _fake_settings())
    sf._mock_session.add.assert_called_once()


# ---------------------------------------------------------------------------
# New rule — correct fields saved
# ---------------------------------------------------------------------------

async def test_new_rule_uses_sender_email_and_keyword_conditions():
    sf = _make_session_factory(existing_rules=[])
    email = FakeEmail(
        from_address="billing@acme.com",
        subject="fatura acme",
        body_text="pagamento fatura",
        tenant_id=42,
        id=99,
    )
    with patch("app.core.system_settings.get_inbox_keywords", return_value=["fatura", "pagamento"]):
        await _auto_save_rule(sf, email, "Invoices", 0.95, _fake_settings())

    sf._mock_session.add.assert_called_once()
    saved = sf._mock_session.add.call_args[0][0]
    assert saved.tenant_id == 42
    assert saved.created_from_email_id == 99
    assert saved.min_match == 2

    cond_types = {c["type"] for c in saved.conditions}
    assert "sender_email" in cond_types
    assert "keyword" in cond_types

    sender_cond = next(c for c in saved.conditions if c["type"] == "sender_email")
    assert sender_cond["value"] == "billing@acme.com"

    keyword_values = {c["value"] for c in saved.conditions if c["type"] == "keyword"}
    assert keyword_values == {"fatura", "pagamento"}

    assert any(a.get("folder") == "Invoices" for a in saved.actions)


async def test_sender_email_lowercased():
    sf = _make_session_factory(existing_rules=[])
    email = FakeEmail(
        from_address="User@ACME.COM",
        subject="invoice here",
        body_text="invoice",
    )
    with patch("app.core.system_settings.get_inbox_keywords", return_value=["invoice"]):
        await _auto_save_rule(sf, email, "Work", 0.92, _fake_settings())

    saved = sf._mock_session.add.call_args[0][0]
    sender_cond = next(c for c in saved.conditions if c["type"] == "sender_email")
    assert sender_cond["value"] == "user@acme.com"


async def test_new_rule_committed():
    sf = _make_session_factory(existing_rules=[])
    email = FakeEmail(
        from_address="user@acme.com",
        subject="fatura",
        body_text="fatura",
    )
    with patch("app.core.system_settings.get_inbox_keywords", return_value=["fatura"]):
        await _auto_save_rule(sf, email, "Work", 0.92, _fake_settings())
    sf._mock_session.commit.assert_called()
