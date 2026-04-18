class ClassificationResult:
    def __init__(self, folder: str, confidence: float):
        self.folder = folder
        self.confidence = confidence
        # Set by classifiers — one of: "rule", "llm", "rules_only_nomatch"
        # "rule_confirmed" and "rule_conflict" are no longer generated;
        # kept in this comment for historical reference only.
        self.source = "llm"
        # Sender identity — extracted by LLM
        self.sender_type = None
        self.sender_name = None
        # Token telemetry
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        # LLM inference time — seconds from sending the HTTP request to receiving the response
        self.llm_time_seconds: float = 0.0
        # Conflict context — set when source == "rule_conflict"
        self.rule_folder = None   # what the rule suggested
        self.llm_folder = None    # what the LLM said instead
        # Suggestion context — set when LLM returns a folder not in the active list
        self.suggested_folder = None  # the unknown folder name the LLM proposed


class EmailClassifier:
    async def classify(self, email) -> ClassificationResult:
        raise NotImplementedError
