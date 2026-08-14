from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(slots=True)
class Signal:
    name: str
    score: int
    reason: str


@dataclass(slots=True)
class Finding:
    source: str
    location: str
    score: int
    severity: Severity
    signals: list[Signal] = field(default_factory=list)
    preview: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    action_override: str | None = None

    @property
    def recommended_action(self) -> str:
        if self.action_override:
            return self.action_override
        if self.score >= 80:
            return "REVIEW / LIKELY DELETE"
        if self.score >= 60:
            return "REVIEW"
        return "KEEP UNLESS CONTEXT CONFIRMS MALICIOUS"


@dataclass(slots=True)
class SiteInventory:
    root: Path
    wordpress_version: str | None
    table_prefix: str | None
    plugins: list[str]
    themes: list[str]
    mu_plugins: list[str]
