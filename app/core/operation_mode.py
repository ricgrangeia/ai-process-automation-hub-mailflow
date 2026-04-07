"""
Operation Mode — controls how the AI worker classifies emails.

The current mode is stored in Redis under OPERATION_MODE_KEY.
The worker reads it once per job so mode changes take effect
on the next email without a restart.

Modes
-----
hybrid      Rules first → LLM fallback (default)
rules_only  Only learned rules fire; unmatched → NeedsReview (zero LLM cost)
llm_only    Always call LLM, skip rule lookup (useful to audit model quality)
auto_learn  Hybrid + high-confidence LLM decisions auto-saved as ai_auto rules
"""

OPERATION_MODE_KEY = "mailai:operation_mode"

DEFAULT_MODE = "hybrid"

# Confidence threshold for auto-saving a rule in auto_learn mode.
# Stricter than the 0.75 move threshold — only very confident decisions.
AUTO_LEARN_CONFIDENCE_THRESHOLD = 0.90

# Domains too generic to create meaningful rules for.
GENERIC_DOMAINS = {
    "gmail.com", "googlemail.com",
    "hotmail.com", "hotmail.co.uk",
    "outlook.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk",
    "icloud.com", "me.com", "mac.com",
    "protonmail.com", "proton.me",
}

MODES = {
    "hybrid":     "🔀 Hybrid — Rules first, LLM fallback",
    "rules_only": "📚 Rules Only — No LLM, unmatched → NeedsReview",
    "llm_only":   "🧠 LLM Only — Always call LLM, skip rules",
    "auto_learn": "🤖 Auto-Learn — Hybrid + auto-save high-confidence decisions",
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
