"""
Classifier pipeline (context-aware LLM).

The LLM is always the decision maker. Rules are not a competing classifier —
they provide memory (sender history, matched keywords) that is injected into
the prompt as context, the same way a human would recall past experience.

Flow:
  1. Build context from rule history + inbox filter keywords
  2. LLM classifies with that context
  3. confidence >= threshold → return result
  4. confidence < threshold  → NeedsReview (Telegram review card)

No "rule wins", no "conflict" escalation. The LLM decides.
"""

from .contracts import ClassificationResult


class HybridClassifier:

    def __init__(self, rule_classifier, llm_classifier, threshold=0.75):
        self.rule = rule_classifier
        self.llm = llm_classifier
        self.threshold = threshold

    async def classify(self, email, folders: list[str] | None = None) -> ClassificationResult:

        # Build context: sender history + matched keywords from rule/filter store
        context = await self.rule.get_context(email)

        # LLM decides with full context
        result = await self.llm.classify(email, context=context, folders=folders)

        if result.confidence >= self.threshold:
            return result

        # Low confidence — ask human
        low = ClassificationResult("NeedsReview", result.confidence)
        low.source = result.source
        low.sender_type = result.sender_type
        low.sender_name = result.sender_name
        low.prompt_tokens = result.prompt_tokens
        low.completion_tokens = result.completion_tokens
        low.total_tokens = result.total_tokens
        low.llm_time_seconds = result.llm_time_seconds
        return low
