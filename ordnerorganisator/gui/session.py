"""Zustand eines Organisationsvorgangs in der Oberfläche."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ai.base import UsageInfo
from ..content import ContentInfo
from ..executor import RunState
from ..models import Snapshot
from ..plan import Operation, Plan
from ..planner import Resolution
from ..secrets_store import SecretStore
from ..settings import AppSettings


@dataclass
class Session:
    settings: AppSettings
    secrets: SecretStore
    snapshot: Snapshot | None = None
    plan: Plan | None = None
    resolution: Resolution | None = None
    #: Beim Prüfen automatisch zurückgewiesene Anweisungen
    rejected_invalid: list[Operation] = field(default_factory=list)
    contents: dict[str, ContentInfo] = field(default_factory=dict)
    content_selection: list[str] = field(default_factory=list)
    run: RunState | None = None
    provider_label: str = ""
    usage: UsageInfo | None = None

    def reset(self) -> None:
        self.snapshot = None
        self.plan = None
        self.resolution = None
        self.rejected_invalid = []
        self.contents = {}
        self.content_selection = []
        self.run = None
        self.provider_label = ""
        self.usage = None
