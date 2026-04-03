class ClassificationResult:
    def __init__(self, folder: str, confidence: float):
        self.folder = folder
        self.confidence = confidence


class EmailClassifier:
    async def classify(self, email) -> ClassificationResult:
        raise NotImplementedError
