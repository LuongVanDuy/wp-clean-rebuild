from .models import Severity


def severity_for(score: int) -> Severity:
    score = max(0, min(100, score))
    if score >= 80:
        return Severity.CRITICAL
    if score >= 60:
        return Severity.HIGH
    if score >= 30:
        return Severity.MEDIUM
    return Severity.LOW
