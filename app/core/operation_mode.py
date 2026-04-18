"""
Operation Mode — controls how the AI worker classifies emails.

The current mode is stored in Redis under OPERATION_MODE_KEY.
The worker reads it once per job so mode changes take effect
on the next email without a restart.

Modes
-----
hybrid      LLM with sender context — rules inform, LLM decides (default)
auto_learn  Same as hybrid + high-confidence decisions auto-saved as rules
rules_only  [legacy] Only learned rules fire; unmatched → NeedsReview
llm_only    [legacy] LLM with no context injection (rules ignored entirely)

The primary distinction is now the supervised/autonomous toggle (Learning Mode),
not the operation mode. hybrid and auto_learn are the recommended modes.
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
    "rules_only": "📚 Rules Only — legacy, no LLM",
    "llm_only":   "🧠 LLM Only — legacy, no context injection",
}


async def get_mode(r) -> str:
    """Read current mode from Redis. Returns DEFAULT_MODE if not set."""
    val = await r.get(OPERATION_MODE_KEY)
    if val and val in MODES:
        return val
    return DEFAULT_MODE


async def set_mode(r, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}. Valid: {list(MODES)}")
    await r.set(OPERATION_MODE_KEY, mode)
