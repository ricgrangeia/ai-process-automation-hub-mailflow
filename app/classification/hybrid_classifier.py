from .contracts import ClassificationResult


class HybridClassifier:
    """
    Classification pipeline:

    1. Rule classifier runs first.

    2a. Rule matched → pass the rule's folder as a hint to the LLM for validation.
        - LLM agrees  (same folder) → source="rule_confirmed", confidence boosted to ≥ 0.95
        - LLM disagrees (different folder) → source="rule_conflict", folder="NeedsReview"
          Human decides — the rule may be wrong for this specific email.

    2b. No rule matched → pure LLM classification.
        - confidence ≥ threshold → action
        - confidence < threshold → NeedsReview

    This means the LLM always runs (never fully bypassed by a rule), so it can
    catch cases where the same sender sends a different type of email than usual.
    """

    def __init__(self, rule_classifier, llm_classifier, threshold=0.75):
        self.rule = rule_classifier
        self.llm = llm_classifier
        self.threshold = threshold

    async def classify(self, email, folders: list[str] | None = None) -> ClassificationResult:

        rule_result = await self.rule.classify(email)

        if rule_result:
            # Rule matched — ask LLM to validate with the rule as context
            llm_result = await self.llm.classify(email, rule_hint=rule_result.folder, folders=folders)

            if llm_result.folder == rule_result.folder:
                # Agreement — rule confirmed by the model
                llm_result.confidence = max(llm_result.confidence, 0.95)
                llm_result.source = "rule_confirmed"
                llm_result.rule_folder = rule_result.folder
                return llm_result

            else:
                # Conflict — rule and model disagree, escalate to human
                conflict = ClassificationResult("NeedsReview", llm_result.confidence)
                conflict.source = "rule_conflict"
                conflict.rule_folder = rule_result.folder
                conflict.llm_folder = llm_result.folder
                conflict.sender_type = llm_result.sender_type
                conflict.sender_name = llm_result.sender_name
                conflict.prompt_tokens = llm_result.prompt_tokens
                conflict.completion_tokens = llm_result.completion_tokens
                conflict.total_tokens = llm_result.total_tokens
                conflict.llm_time_seconds = llm_result.llm_time_seconds
                return conflict

        # No rule — pure LLM
        llm_result = await self.llm.classify(email, folders=folders)

        if llm_result.confidence >= self.threshold:
            return llm_result

        low = ClassificationResult("NeedsReview", llm_result.confidence)
        low.source = llm_result.source
        low.sender_type = llm_result.sender_type
        low.sender_name = llm_result.sender_name
        low.prompt_tokens = llm_result.prompt_tokens
        low.completion_tokens = llm_result.completion_tokens
        low.total_tokens = llm_result.total_tokens
        low.llm_time_seconds = llm_result.llm_time_seconds
        return low
