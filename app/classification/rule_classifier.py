from .contracts import ClassificationResult


class RuleClassifier:

    async def classify(self, email):

        subject = (email.subject or "").lower()
        body = (email.body_text or "").lower()

        if "invoice" in subject or "fatura" in body:
            return ClassificationResult("Invoices", 1.0)

        if "unsubscribe" in body:
            return ClassificationResult("Marketing", 1.0)

        return None  # no rule matched
