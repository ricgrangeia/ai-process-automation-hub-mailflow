class ClassificationResult:
    def __init__(self, folder: str, confidence: float):
        self.folder = folder
        self.confidence = confidence
        # Set by classifiers — one of: "rule", "rule_confirmed", "rule_conflict", "llm", "rules_only_nomatch"
        self.source = "llm"
        # Sender identity — extracted by LLM
        self.sender_type = None
        self.sender_name = None
        # Token telemetry
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        # Conflict context — set when source == "rule_conflict"
        self.rule_folder = None   # what the rule suggested
        self.llm_folder = None    # what the LLM said instead


class EmailClassifier:
    async def classify(self, email) -> ClassificationResult:
        raise NotImplementedError
