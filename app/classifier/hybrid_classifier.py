class HybridClassifier:

    def __init__(self, rule_classifier, llm_classifier, threshold=0.75):
        self.rule = rule_classifier
        self.llm = llm_classifier
        self.threshold = threshold

    async def classify(self, email):

        rule_result = await self.rule.classify(email)

        if rule_result:
            return rule_result

        llm_result = await self.llm.classify(email)

        if llm_result.confidence >= self.threshold:
            return llm_result

        return ClassificationResult("NeedsReview", llm_result.confidence)