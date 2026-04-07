"""
Tests for _auto_save_rule() in app/processing/worker.py

Covers:
  - Generic domain skip
  - Missing domain skip
  - Human rule is never overwritten
  - Existing ai_auto rule: increments hit_count, no duplicate created
  - New rule saved with correct fields
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, call

from app.processing.worker import _auto_save_rule
from app.core.operation_mode import GENERIC_DOMAINS
from tests.conftest import FakeEmail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_factory(existing_rule=None):
    """
    Builds a session_factory whose session.execute() returns existing_rule
    (or None) for the SELECT query, and records any session.add() calls.
    """
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none.return_value = existing_rule

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_scalar)
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)

    session_factory = MagicMock(return_value=cm)
    session_factory._mock_session = mock_session  # expose for assertions
    return session_factory


def _human_rule(domain="amazon.com"):
    rule = MagicMock()
    rule.source = "human"
    rule.match_value = domain
    rule.hit_count = 5
    return rule


def _ai_rule(domain="amazon.com"):
    rule = MagicMock()
    rule.source = "ai_auto"
    rule.match_value = domain
    rule.hit_count = 2
    return rule


# ---------------------------------------------------------------------------
# Generic / missing domain — must skip without touching the DB
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", sorted(GENERIC_DOMAINS))
async def test_generic_domain_skipped(domain):
    sf = _make_session_factory()
    email = FakeEmail(from_address=f"user@{domain}")
    await _auto_save_rule(sf, email, "Work", 0.95)
    sf._mock_session.execute.assert_not_called()
    sf._mock_session.add.assert_not_called()


async def test_missing_domain_skipped():
    sf = _make_session_factory()
    email = FakeEmail(from_address="no-at-sign")
    await _auto_save_rule(sf, email, "Work", 0.95)
    sf._mock_session.execute.assert_not_called()


async def test_none_from_address_skipped():
    sf = _make_session_factory()
    email = FakeEmail(from_address=None)
    await _auto_save_rule(sf, email, "Work", 0.95)
    sf._mock_session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Human rule — must never be overwritten
# ---------------------------------------------------------------------------

async def test_human_rule_not_overwritten():
    sf = _make_session_factory(existing_rule=_human_rule("amazon.com"))
    email = FakeEmail(from_address="orders@amazon.com")
    await _auto_save_rule(sf, email, "Invoices", 0.95)
    sf._mock_session.add.assert_not_called()
    sf._mock_session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Existing ai_auto rule — increment hit_count, no new rule
# ---------------------------------------------------------------------------

async def test_existing_ai_rule_increments_hit_count():
    ai_rule = _ai_rule("amazon.com")
    sf = _make_session_factory(existing_rule=ai_rule)
    email = FakeEmail(from_address="orders@amazon.com")
    await _auto_save_rule(sf, email, "Invoices", 0.95)
    assert ai_rule.hit_count == 3
    sf._mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# New rule — correct fields saved
# ---------------------------------------------------------------------------

async def test_new_rule_created_with_correct_source():
    sf = _make_session_factory(existing_rule=None)
    email = FakeEmail(from_address="billing@acme.com", tenant_id=42, id=99)
    await _auto_save_rule(sf, email, "Invoices", 0.95)

    sf._mock_session.add.assert_called_once()
    saved = sf._mock_session.add.call_args[0][0]
    assert saved.source == "ai_auto"
    assert saved.tenant_id == 42
    assert saved.match_field == "sender_domain"
    assert saved.match_value == "acme.com"
    assert saved.created_from_email_id == 99
    assert any(a.get("folder") == "Invoices" for a in saved.actions)


async def test_new_rule_domain_lowercased():
    sf = _make_session_factory(existing_rule=None)
    email = FakeEmail(from_address="user@ACME.COM")
    await _auto_save_rule(sf, email, "Work", 0.92)

    saved = sf._mock_session.add.call_args[0][0]
    assert saved.match_value == "acme.com"


async def test_new_rule_committed():
    sf = _make_session_factory(existing_rule=None)
    email = FakeEmail(from_address="user@acme.com")
    await _auto_save_rule(sf, email, "Work", 0.92)
    sf._mock_session.commit.assert_called()
