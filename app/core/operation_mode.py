"""
Operation Mode — controls auto-learn behaviour.

Stored in Redis under OPERATION_MODE_KEY.
Read once per job — mode changes take effect on the next email, no restart needed.

Modes
-----
hybrid      LLM with sender context — rules inform, LLM decides (default)
auto_learn  Same as hybrid + high-confidence decisions auto-saved as rules

The key operational distinction is the supervised/autonomous toggle (Learning Mode),
not the operation mode. Use hybrid unless you want automatic rule creation.
"""

OPERATION_MODE_KEY = "mailai:operation_mode"

DEFAULT_MODE = "hybrid"

# Confidence threshold for auto-saving a rule in auto_learn mode.
AUTO_LEARN_CONFIDENCE_THRESHOLD = 0.90

# Freemail domains — too generic for meaningful sender-scoped rules
GENERIC_DOMAINS = {
    "gmail.com", "googlemail.com",
    "hotmail.com", "hotmail.co.uk",
    "outlook.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk",
    "icloud.com", "me.com", "mac.com",
    "protonmail.com", "proton.me",
}

MODES = {
    "hybrid":     "🔀 Hybrid — LLM with sender context (recommended)",
    "auto_learn": "🤖 Auto-Learn — Hybrid + auto-save high-confidence decisions",
}


async def get_mode(r) -> str:
    """Read current mode from Redis. Returns DEFAULT_MODE if not set or unrecognised."""
    val = await r.get(OPERATION_MODE_KEY)
    if val and val in MODES:
        return val
    return DEFAULT_MODE


async def set_mode(r, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode!r}. Valid: {list(MODES)}")
    await r.set(OPERATION_MODE_KEY, mode)
