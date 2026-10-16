"""Gemeinsame Schnittstelle aller KI-Anbieter."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

from ..models import Snapshot
from ..plan import Plan
from ..settings import UserRules

ProgressFn = Callable[[str], None]


class ProviderError(Exception):
    """Fehler eines KI-Anbieters mit verständlicher (deutscher) Meldung."""


class AnalysisCancelled(Exception):
    pass


@dataclass
class AnalysisRequest:
    snapshot: Snapshot
    rules: UserRules
    #: Ergebnisse der (optionalen) Inhaltsanalyse – nur für freigegebene Dateien
    contents: dict = field(default_factory=dict)
    include_hidden: bool = False
    include_absolute_root: bool = False
    max_package_chars: int = 60000


@dataclass
class UsageInfo:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    requests: int = 0

    def add(self, other: "UsageInfo") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.requests += other.requests
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + other.cost_usd


class Provider(ABC):
    id: str = ""
    name: str = ""
    #: True, wenn Daten das Gerät verlassen (Internet)
    is_external: bool = False

    def __init__(self) -> None:
        self.last_usage = UsageInfo()

    def describe(self) -> str:
        return self.name

    @abstractmethod
    def analyze(
        self,
        request: AnalysisRequest,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Plan:
        ...
