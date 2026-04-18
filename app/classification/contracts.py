class ClassificationResult:
    def __init__(self, folder: str, confidence: float):
        self.folder = folder
        self.confidence = confidence
        # Set by classifiers — one of: "llm", "rule" (rules_only mode only)
        self.source = "llm"
        # Sender identity — extracted by LLM
        self.sender_type = None
        self.sender_name = None
        # Token telemetry
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        # LLM inference time in seconds
        self.llm_time_seconds: float = 0.0
        # Set when LLM returns a folder not in the active list
        self.suggested_folder = None


class EmailClassifier:
    async def classify(self, email) -> ClassificationResult:
        raise NotImplementedError
