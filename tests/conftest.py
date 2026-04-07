"""
Shared fixtures used across all test modules.
"""
import pytest


class FakeEmail:
    """Minimal email object that satisfies classifier interfaces."""

    def __init__(
        self,
        id: int = 1,
        tenant_id: int = 1,
        account_id: int = 1,
        from_address: str = "sender@amazon.com",
        subject: str = "Your invoice #123",
        body_text: str = "Please find attached your invoice.",
        imap_uid: str = "42",
    ):
        self.id = id
        self.tenant_id = tenant_id
        self.account_id = account_id
        self.from_address = from_address
        self.subject = subject
        self.body_text = body_text
        self.imap_uid = imap_uid


class FakeSettings:
    llm_base_url = "http://llm-test"
    llm_api_key = "test-key"
    llm_model = "qwen2.5-7b-instruct"
    master_key = "test-master-key-for-unit-tests"


@pytest.fixture
def fake_email():
    return FakeEmail()


@pytest.fixture
def fake_settings():
    return FakeSettings()
