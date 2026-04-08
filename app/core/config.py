import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Core infrastructure
    database_url: str
    redis_url: str
    storage_root: str

    # Encryption
    master_key: str

    # LLM configuration (required by ai-worker and query-worker; optional elsewhere)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # Worker behavior
    poll_interval_sec: int = 240
    max_unseen_per_cycle: int = 20
    inbox_folder: str = "INBOX"
    mark_seen_after_store: bool = True

    # Telegram (optional — leave empty to disable)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # AI Tool Server (PDF QR decode, web search, etc.)
    tool_server_url: str = ""
    tool_server_api_key: str = ""

    # SMTP — required for query result emails
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    report_recipient: str = ""


def get_settings() -> Settings:
    return Settings(
        # Required infra
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        storage_root=os.environ.get("STORAGE_ROOT", "/storage"),

        # Encryption key
        master_key=os.environ["MASTER_KEY"],

        # LLM (required by ai-worker and query-worker; leave empty for other services)
        llm_base_url=os.environ.get("LLM_BASE_URL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),

        # Optional behavior config
        poll_interval_sec=int(os.environ.get("POLL_INTERVAL_SEC", "240")),
        max_unseen_per_cycle=int(os.environ.get("MAX_UNSEEN_PER_CYCLE", "20")),
        inbox_folder=os.environ.get("INBOX_FOLDER", "INBOX"),
        mark_seen_after_store=os.environ.get("MARK_SEEN_AFTER_STORE", "true").lower() == "true",

        # AI Tool Server
        tool_server_url=os.environ.get("TOOL_SERVER_URL", ""),
        tool_server_api_key=os.environ.get("TOOL_SERVER_API_KEY", ""),

        # Telegram
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),

        # SMTP
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        report_recipient=os.environ.get("REPORT_RECIPIENT", ""),
    )
