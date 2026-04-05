"""
Base interface for all email actions.

To add a new action type:
1. Create a new file in processing/actions/
2. Subclass EmailAction and implement execute()
3. Register it in the REGISTRY dict in this file
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.messages.models import EmailMessage
    from app.accounts.models import EmailAccount
    from app.core.config import Settings


class EmailAction:
    async def execute(self, email: "EmailMessage", account: "EmailAccount", settings: "Settings") -> bool:
        raise NotImplementedError


def get_action(config: dict) -> EmailAction:
    """Factory — returns the right EmailAction for a given action config dict."""
    from app.processing.actions.move_folder import MoveFolderAction
    from app.processing.actions.export_pdf import ExportPdfAction

    REGISTRY = {
        "move_folder": MoveFolderAction,
        "export_pdf":  ExportPdfAction,
    }

    action_type = config.get("type")
    cls = REGISTRY.get(action_type)
    if not cls:
        raise ValueError(f"Unknown action type: {action_type!r}")
    return cls(config)
