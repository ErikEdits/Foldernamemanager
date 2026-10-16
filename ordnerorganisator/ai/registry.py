"""Erzeugt den passenden KI-Anbieter aus den Einstellungen."""

from __future__ import annotations

from ..secrets_store import SecretStore, api_key_name
from ..settings import AppSettings
from .base import Provider, ProviderError
from .engine import BuiltinEngine
from .gguf import GGUFProvider
from .openai_compat import ApiProvider


def make_provider(settings: AppSettings, secrets: SecretStore | None = None) -> Provider:
    kind = settings.provider
    if kind == "builtin":
        return BuiltinEngine()
    if kind == "api":
        key = secrets.get(api_key_name(settings.api.provider_kind)) if secrets else None
        return ApiProvider(
            settings.api.base_url,
            settings.api.model,
            api_key=key,
            external_allowed=settings.external_ai_enabled,
            timeout=settings.api.timeout_s,
        )
    if kind == "local_model":
        lm = settings.local_model
        if lm.kind == "gguf":
            return GGUFProvider(lm.gguf_path, lm.context_size)
        return ApiProvider(lm.base_url, lm.model, external_allowed=settings.external_ai_enabled,
                           label="Lokale KI-Laufzeit")
    raise ProviderError("Im Export/Import-Modus erstellt die Cloud-KI den Plan – hier gibt es keinen direkten Anbieter.")


def describe_active(settings: AppSettings) -> str:
    """Kurzbeschreibung für die Statusleiste (aktiver Anbieter und Modell)."""
    if settings.provider == "builtin":
        return "KI: Integrierte KI (offline, kostenlos)"
    if settings.provider == "api":
        name = "OpenRouter" if "openrouter.ai" in settings.api.base_url else settings.api.base_url
        state = "aktiv" if settings.external_ai_enabled else "deaktiviert"
        return f"KI: {name} – Modell: {settings.api.model or '–'} (externe KI {state})"
    if settings.provider == "local_model":
        lm = settings.local_model
        if lm.kind == "gguf":
            return f"KI: Lokale Modelldatei {lm.gguf_path.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1] or '–'}"
        return f"KI: Lokale Laufzeit {lm.base_url} – Modell: {lm.model or '–'}"
    return "KI: Cloud-KI per Export/Import (kein API-Key nötig)"
