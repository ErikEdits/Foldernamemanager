"""Anbindung an OpenAI-kompatible Schnittstellen.

* **OpenRouter** (https://openrouter.ai) – viele Modelle, darunter kostenlose (Limits beachten),
* **lokale Laufzeiten** mit OpenAI-kompatibler API, z. B. llama.cpp-Server, LM Studio,
  Jan, KoboldCpp oder Ollama – vollständig offline nutzbar.

Es werden nur Standardbibliotheken verwendet (keine zusätzlichen Abhängigkeiten).
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .. import APP_NAME, __version__
from ..plan import Plan, PlanFormatError
from ..redaction import redact
from .base import AnalysisCancelled, AnalysisRequest, ProgressFn, Provider, ProviderError, UsageInfo
from .exchange import import_plan_text
from .prompt import SYSTEM_PROMPT, build_packages

OPENROUTER_URL = "https://openrouter.ai/api/v1"


def is_local_url(url: str) -> bool:
    """True für Adressen auf diesem Rechner bzw. im lokalen Netz (keine Daten ins Internet)."""
    host = (urllib.parse.urlparse(url).hostname or "").strip("[]").lower()
    if host in ("localhost", "") or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    context_length: int | None = None
    prompt_price: float | None = None  # USD je Token
    completion_price: float | None = None

    @property
    def is_free(self) -> bool:
        return self.id.endswith(":free") or (self.prompt_price == 0 and self.completion_price == 0)

    def price_label(self) -> str:
        if self.is_free:
            return "kostenlos (Nutzungslimits des Anbieters)"
        if self.prompt_price is None:
            return "Preis unbekannt"
        return (
            f"{self.prompt_price * 1e6:.2f} $ / 1 Mio. Eingabe-Tokens, "
            f"{(self.completion_price or 0) * 1e6:.2f} $ / 1 Mio. Ausgabe-Tokens"
        ).replace(".", ",")

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        if self.is_free:
            return 0.0
        if self.prompt_price is None:
            return None
        return prompt_tokens * self.prompt_price + completion_tokens * (self.completion_price or 0)


class HttpProviderError(ProviderError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


@dataclass
class ChatResult:
    text: str
    usage: UsageInfo
    model: str = ""


def _friendly_http_error(code: int, body: str, local: bool) -> str:
    detail = ""
    try:
        data = json.loads(body)
        err = data.get("error", data)
        detail = err.get("message") if isinstance(err, dict) else str(err)
    except (ValueError, AttributeError):
        detail = body[:300]
    detail = redact(str(detail or "")).strip()
    base = {
        400: "Die Anfrage wurde abgelehnt (ungültige Parameter oder Modell unterstützt die Anfrage nicht).",
        401: "Der API-Key ist ungültig oder fehlt.",
        402: "Das Guthaben beim Anbieter reicht nicht aus.",
        403: "Zugriff verweigert – das Modell ist für diesen Schlüssel nicht freigeschaltet.",
        404: "Modell oder Adresse nicht gefunden.",
        408: "Zeitüberschreitung beim Anbieter.",
        413: "Das Analysepaket ist zu groß für dieses Modell. Verringern Sie die Paketgröße in den Einstellungen.",
        429: "Nutzungslimit erreicht (zu viele Anfragen). Kostenlose Modelle haben oft Minuten- oder Tageslimits – "
             "bitte später erneut versuchen oder ein anderes Modell wählen.",
        500: "Interner Fehler beim Anbieter.",
        502: "Der Anbieter bzw. das Modell ist derzeit nicht erreichbar.",
        503: "Der Dienst ist vorübergehend überlastet.",
    }.get(code, f"Fehler vom Server (HTTP {code}).")
    if local and code in (404,):
        base = "Modell oder Adresse nicht gefunden – läuft die lokale Laufzeit und ist das Modell geladen?"
    return f"{base}{' Details: ' + detail if detail else ''}"


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip() or None
        self.timeout = timeout
        self.local = is_local_url(self.base_url)
        self.is_openrouter = "openrouter.ai" in self.base_url

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"{APP_NAME}/{__version__}",
        }
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.is_openrouter:
            h["X-Title"] = APP_NAME
        return h

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            raise HttpProviderError(_friendly_http_error(exc.code, body, self.local), exc.code) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ProviderError("Zeitüberschreitung – der Server antwortet nicht.") from None
            if self.local:
                raise ProviderError(
                    f"Die lokale KI unter {self.base_url} ist nicht erreichbar. Bitte die Laufzeit "
                    "(z. B. llama.cpp-Server oder LM Studio) starten."
                ) from None
            raise ProviderError(f"Keine Verbindung zum Anbieter: {redact(str(reason))}") from None
        except (socket.timeout, TimeoutError):
            raise ProviderError("Zeitüberschreitung – der Server antwortet nicht.") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ProviderError("Der Server hat keine gültige JSON-Antwort geliefert.") from None

    def list_models(self) -> list[ModelInfo]:
        data = self._request("GET", "models")
        items = data.get("data", data if isinstance(data, list) else [])
        out = []
        for m in items:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            pricing = m.get("pricing") or {}

            def num(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            out.append(ModelInfo(
                id=str(m["id"]),
                name=str(m.get("name") or m["id"]),
                context_length=m.get("context_length"),
                prompt_price=num(pricing.get("prompt")),
                completion_price=num(pricing.get("completion")),
            ))
        out.sort(key=lambda x: (not x.is_free, x.name.casefold()))
        return out

    def chat(self, messages: list[dict], model: str, json_mode: bool = True,
             temperature: float = 0.2, max_tokens: int | None = None) -> ChatResult:
        payload: dict = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.is_openrouter:
            payload["usage"] = {"include": True}
        try:
            data = self._request("POST", "chat/completions", payload)
        except HttpProviderError as exc:
            # Manche Modelle unterstützen den JSON-Modus nicht – dann ohne erneut versuchen.
            if json_mode and exc.status in (400, 422):
                payload.pop("response_format", None)
                data = self._request("POST", "chat/completions", payload)
            else:
                raise
        if "error" in data and not data.get("choices"):
            raise ProviderError(_friendly_http_error(500, json.dumps(data), self.local))
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Die Antwort des Modells hat ein unerwartetes Format.") from None
        u = data.get("usage") or {}
        usage = UsageInfo(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            cost_usd=float(u["cost"]) if isinstance(u.get("cost"), (int, float)) else None,
            requests=1,
        )
        return ChatResult(text=text, usage=usage, model=str(data.get("model") or model))


class ApiProvider(Provider):
    """KI über eine OpenAI-kompatible API (OpenRouter oder lokaler Server)."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 external_allowed: bool = False, timeout: float = 180.0, label: str | None = None):
        super().__init__()
        self.client = OpenAICompatibleClient(base_url, api_key, timeout)
        self.model = model
        self.external_allowed = external_allowed
        self.is_external = not self.client.local
        self.id = "openrouter" if self.client.is_openrouter else ("local_server" if self.client.local else "api")
        self.name = label or ("OpenRouter" if self.client.is_openrouter else
                              ("Lokale KI-Laufzeit" if self.client.local else "API-Anbieter"))

    def describe(self) -> str:
        return f"{self.name} – Modell: {self.model or '(nicht gewählt)'}"

    def analyze(self, request: AnalysisRequest, progress: ProgressFn | None = None,
                cancel: threading.Event | None = None) -> Plan:
        if self.is_external and not self.external_allowed:
            raise ProviderError("Externe KI ist deaktiviert. Aktivieren Sie sie in der KI-Auswahl ausdrücklich.")
        if not self.model:
            raise ProviderError("Bitte zuerst ein Modell auswählen.")
        if self.client.is_openrouter and not self.client.api_key:
            raise ProviderError("Für OpenRouter wird ein API-Key benötigt.")
        packages, _summary = build_packages(request, for_chat=False)
        merged: Plan | None = None
        self.last_usage = UsageInfo()
        for pkg in packages:
            if cancel is not None and cancel.is_set():
                raise AnalysisCancelled()
            if progress:
                progress(f"Anfrage an {self.name} ({pkg.title}) …")
            result = self.client.chat(
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": pkg.text}],
                model=self.model,
            )
            self.last_usage.add(result.usage)
            try:
                plan = import_plan_text(result.text, origin=f"{self.id}:{self.model}", expected_package_id=pkg.package_id)
            except PlanFormatError as exc:
                raise ProviderError(f"Die KI hat keinen gültigen Plan geliefert ({pkg.title}): {exc}") from None
            if merged is None:
                merged = plan
            else:
                merged.merge(plan)
        assert merged is not None
        merged.provider = self.id
        merged.model = self.model
        for op in merged.operations:
            op.origin = f"{self.id}:{self.model}"
            if op.confidence is not None and op.confidence < 0.5:
                op.accepted = False
        return merged
