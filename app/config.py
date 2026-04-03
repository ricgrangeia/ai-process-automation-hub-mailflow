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

    # LLM configuration
    llm_base_url: str
    llm_api_key: str
    llm_model: str

    # Worker behavior
    poll_interval_sec: int = 240
    max_unseen_per_cycle: int = 20
    inbox_folder: str = "INBOX"
    mark_seen_after_store: bool = True


def get_settings() -> Settings:
    return Settings(
        # Required infra
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        storage_root=os.environ.get("STORAGE_ROOT", "/storage"),

        # Encryption key
        master_key=os.environ["MASTER_KEY"],

        # Required LLM fields
        llm_base_url=os.environ["LLM_BASE_URL"],
        llm_api_key=os.environ["LLM_API_KEY"],
        llm_model=os.environ["LLM_MODEL"],

        # Optional behavior config
        poll_interval_sec=int(os.environ.get("POLL_INTERVAL_SEC", "240")),
        max_unseen_per_cycle=int(os.environ.get("MAX_UNSEEN_PER_CYCLE", "20")),
        inbox_folder=os.environ.get("INBOX_FOLDER", "INBOX"),
        mark_seen_after_store=os.environ.get("MARK_SEEN_AFTER_STORE", "true").lower() == "true",
    )