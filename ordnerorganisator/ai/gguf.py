"""Lokale Modelldatei (GGUF) direkt im Programm ausführen – optional.

Benötigt das Zusatzpaket ``llama-cpp-python``. Ohne dieses Paket steht die
Option nicht zur Verfügung; die integrierte KI funktioniert unabhängig davon.
"""

from __future__ import annotations

import os
import threading

from ..plan import Plan, PlanFormatError
from .base import AnalysisCancelled, AnalysisRequest, ProgressFn, Provider, ProviderError, UsageInfo
from .exchange import import_plan_text
from .prompt import SYSTEM_PROMPT, build_packages

_MODEL_CACHE: dict[tuple[str, int], object] = {}


def llama_available() -> bool:
    try:
        import llama_cpp  # noqa: F401

        return True
    except Exception:
        return False


class GGUFProvider(Provider):
    id = "gguf"
    is_external = False

    def __init__(self, model_path: str, context_size: int = 8192):
        super().__init__()
        self.model_path = model_path
        self.context_size = context_size
        self.name = f"Lokale Modelldatei ({os.path.basename(model_path) or 'keine gewählt'})"

    def _load(self):
        try:
            from llama_cpp import Llama
        except Exception:
            raise ProviderError(
                "Für Modelldateien wird das Zusatzpaket „llama-cpp-python“ benötigt "
                "(pip install llama-cpp-python). Alternativ eine lokale Laufzeit wie LM Studio nutzen."
            ) from None
        if not self.model_path or not os.path.isfile(self.model_path):
            raise ProviderError("Die Modelldatei wurde nicht gefunden. Bitte eine .gguf-Datei auswählen.")
        key = (self.model_path, self.context_size)
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key] = Llama(model_path=self.model_path, n_ctx=self.context_size, verbose=False)
        return _MODEL_CACHE[key]

    def analyze(self, request: AnalysisRequest, progress: ProgressFn | None = None,
                cancel: threading.Event | None = None) -> Plan:
        if progress:
            progress("Modell wird geladen …")
        llm = self._load()
        # Pakete passend zum Kontextfenster des Modells
        request.max_package_chars = min(request.max_package_chars, int(self.context_size * 3.5 * 0.55))
        packages, _ = build_packages(request, for_chat=False)
        merged: Plan | None = None
        self.last_usage = UsageInfo()
        for pkg in packages:
            if cancel is not None and cancel.is_set():
                raise AnalysisCancelled()
            if progress:
                progress(f"Lokales Modell analysiert ({pkg.title}) …")
            out = llm.create_chat_completion(  # type: ignore[attr-defined]
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": pkg.text}],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            text = out["choices"][0]["message"]["content"] or ""
            u = out.get("usage") or {}
            self.last_usage.add(UsageInfo(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), 0.0, 1))
            try:
                plan = import_plan_text(text, origin="gguf", expected_package_id=pkg.package_id)
            except PlanFormatError as exc:
                raise ProviderError(f"Das lokale Modell hat keinen gültigen Plan geliefert: {exc}") from None
            if merged is None:
                merged = plan
            else:
                merged.merge(plan)
        assert merged is not None
        merged.provider = "gguf"
        merged.model = os.path.basename(self.model_path)
        for op in merged.operations:
            op.origin = "gguf"
            if op.confidence is not None and op.confidence < 0.5:
                op.accepted = False
        return merged
