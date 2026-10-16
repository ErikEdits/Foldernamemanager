from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ordnerorganisator.ai.base import AnalysisRequest, ProviderError
from ordnerorganisator.ai.exchange import export_packages, extract_json_text, import_plan_files, import_plan_text
from ordnerorganisator.ai.openai_compat import ApiProvider, OpenAICompatibleClient, is_local_url
from ordnerorganisator.ai.prompt import build_packages
from ordnerorganisator.ai.registry import describe_active, make_provider
from ordnerorganisator.plan import PlanFormatError
from ordnerorganisator.planner import auto_reject_invalid
from ordnerorganisator.redaction import REDACTOR, redact
from ordnerorganisator.scanner import scan_folder
from ordnerorganisator.secrets_store import SecretStore, _MemoryBackend
from ordnerorganisator.settings import AppSettings, UserRules

from conftest import make_tree

SECRET = "sk-or-v1-" + "a1b2c3d4" * 8


# -- Analysepaket -------------------------------------------------------------

def test_package_contents_and_privacy(example_project):
    snap = scan_folder(example_project)
    (example_project / "desktop.ini").write_text("x")
    snap = scan_folder(example_project)
    pkgs, privacy = build_packages(AnalysisRequest(snap, UserRules()))
    assert len(pkgs) == 1
    text = pkgs[0].text
    for line in ("Downloads neu/", "Downloads neu/setup.exe", "datei1.txt", "final_final_v2.pdf", "Bilder/urlaub.jpg"):
        assert f"\n{line}\n" in text
    assert "desktop.ini" not in text  # versteckte/System-Dateien standardmäßig nicht
    assert str(example_project) not in text  # kein absoluter Pfad (Benutzername!)
    assert "package_id: " + pkgs[0].package_id in text
    assert '"operations"' in text and "Löschen" in text
    assert privacy.files == 5 and privacy.absolute_root is None
    assert any("Keine Dateiinhalte" in line for line in privacy.lines())


def test_package_never_contains_secrets(tmp_path):
    REDACTOR.register(SECRET)
    root = make_tree(tmp_path / "R", {f"notiz {SECRET}.txt": "x"})
    pkgs, _ = build_packages(AnalysisRequest(scan_folder(root), UserRules(custom_instructions=f"key {SECRET}")))
    assert SECRET not in pkgs[0].text
    assert "[ENTFERNT]" in pkgs[0].text
    files = export_packages(pkgs, "R", tmp_path)
    assert SECRET not in files[0].read_text(encoding="utf-8")


def test_large_structure_is_split(tmp_path):
    spec = {f"Ordner{i:02d}": {f"datei_{j:03d}_mit_langem_namen.txt": "x" for j in range(60)} for i in range(12)}
    root = make_tree(tmp_path / "R", spec)
    snap = scan_folder(root)
    pkgs, privacy = build_packages(AnalysisRequest(snap, UserRules(), max_package_chars=15000))
    assert len(pkgs) > 1 and privacy.parts == len(pkgs)
    assert all(len(p.text) <= 15000 + 2000 for p in pkgs)
    listed = []
    for p in pkgs:
        body = p.text.split("```text\n", 1)[1].split("\n```", 1)[0]
        listed.extend(body.splitlines())
        assert f"Teil {p.part} von {len(pkgs)}" in p.text
    assert len([x for x in listed if x.endswith(".txt")]) == 720
    assert len(set(listed)) == len(listed)


def test_protected_dirs_collapsed(tmp_path):
    root = make_tree(tmp_path / "R", {"node_modules": {"a": {"b.js": "1"}}, "x.txt": "1"})
    pkgs, privacy = build_packages(AnalysisRequest(scan_folder(root), UserRules()))
    assert "node_modules/   [geschützt" in pkgs[0].text
    assert "node_modules/a/b.js" not in pkgs[0].text
    assert privacy.protected_collapsed == 1


# -- Import --------------------------------------------------------------------

CHAT_ANSWER = """Gern! Hier ist der Plan:

```json
{"package_id": "abc", "summary": "Test", "operations": [
  {"id": "op1", "type": "move", "source": "datei1.txt", "target": "Dokumente/datei1.txt", "reason": "Dokument", "confidence": 0.8},
  {"id": "op2", "type": "rename", "source": "Downloads neu", "target": "Downloads_Neu"},
  {"id": "op3", "type": "delete", "source": "Bilder"},
  {"id": "op4", "type": "move", "source": "Bilder/urlaub.jpg", "target": "../../Windows/urlaub.jpg"}
], "questions": ["Was ist in datei1.txt?"]}
```
Viel Erfolg!"""


def test_import_chat_answer_and_validate(example_project):
    snap = scan_folder(example_project)
    plan = import_plan_text(CHAT_ANSWER, expected_package_id="xyz")
    assert len(plan.operations) == 4
    assert plan.questions == ["Was ist in datei1.txt?"]
    assert any("package_id" in n for n in plan.import_notes)
    res, rejected = auto_reject_invalid(snap, plan)
    assert {op.id for op in rejected} == {"op3", "op4"}
    assert not res.has_errors
    assert res.file_map["datei1.txt"] == "Dokumente/datei1.txt"


def test_extract_json_variants():
    assert json.loads(extract_json_text('﻿{"operations": []}')) == {"operations": []}
    assert json.loads(extract_json_text('Text davor {"operations": []} Text danach')) == {"operations": []}
    with pytest.raises(PlanFormatError):
        extract_json_text("Leider kann ich das nicht.")
    with pytest.raises(PlanFormatError):
        extract_json_text('{"operations": [ kaputt }')


def test_import_multiple_parts(tmp_path):
    a = tmp_path / "teil1.json"
    b = tmp_path / "teil2.json"
    a.write_text('{"operations": [{"type": "move", "source": "a.txt", "target": "X/a.txt"}]}', encoding="utf-8")
    b.write_text('{"operations": [{"type": "move", "source": "b.txt", "target": "X/b.txt"}]}', encoding="utf-8")
    plan = import_plan_files([a, b])
    assert [op.source for op in plan.operations] == ["a.txt", "b.txt"]
    assert len({op.id for op in plan.operations}) == 2


# -- API (mit lokalem Test-Server) ---------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    calls: list = []
    status = 200
    reject_json_mode = False

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _Handler.calls.append(("GET", self.path, dict(self.headers), None))
        self._send(200, {"data": [
            {"id": "meta/llama:free", "name": "Llama (free)", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "big/model", "name": "Big", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        ]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.calls.append(("POST", self.path, dict(self.headers), payload))
        if _Handler.status != 200:
            self._send(_Handler.status, {"error": {"message": "Rate limit exceeded"}})
            return
        if _Handler.reject_json_mode and "response_format" in payload:
            self._send(400, {"error": {"message": "response_format not supported"}})
            return
        user = payload["messages"][1]["content"]
        pid = user.split("package_id: ", 1)[1].split("\n", 1)[0]
        plan = {"package_id": pid, "operations": [
            {"type": "move", "source": "datei1.txt", "target": "Dokumente/datei1.txt", "confidence": 0.9},
            {"type": "rename", "source": "Bilder", "target": "Images", "confidence": 0.3},
        ]}
        self._send(200, {"model": payload["model"], "choices": [{"message": {"content": json.dumps(plan)}}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.0001}})


@pytest.fixture
def server():
    _Handler.calls = []
    _Handler.status = 200
    _Handler.reject_json_mode = False
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_local_server_provider(server, example_project, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    client = OpenAICompatibleClient(server)
    models = client.list_models()
    assert models[0].is_free and models[0].price_label().startswith("kostenlos")
    assert models[1].estimate_cost(1_000_000, 0) == pytest.approx(1.0)

    prov = ApiProvider(server, "meta/llama:free")
    assert not prov.is_external
    snap = scan_folder(example_project)
    plan = prov.analyze(AnalysisRequest(snap, UserRules()))
    assert [op.target for op in plan.operations] == ["Dokumente/datei1.txt", "Images"]
    assert plan.operations[0].accepted and not plan.operations[1].accepted  # geringe Sicherheit
    assert prov.last_usage.prompt_tokens == 100 and prov.last_usage.cost_usd == pytest.approx(0.0001)
    post = [c for c in _Handler.calls if c[0] == "POST"][0]
    assert post[3]["response_format"] == {"type": "json_object"}
    assert "Authorization" not in post[2]


def test_json_mode_fallback_and_errors(server, example_project, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    snap = scan_folder(example_project)
    _Handler.reject_json_mode = True
    plan = ApiProvider(server, "m").analyze(AnalysisRequest(snap, UserRules()))
    assert len(plan.operations) == 2
    _Handler.reject_json_mode = False
    _Handler.status = 429
    with pytest.raises(ProviderError, match="Nutzungslimit"):
        ApiProvider(server, "m").analyze(AnalysisRequest(snap, UserRules()))


def test_external_ai_requires_opt_in(example_project):
    snap = scan_folder(example_project)
    prov = ApiProvider("https://openrouter.ai/api/v1", "x", api_key=SECRET, external_allowed=False)
    assert prov.is_external
    with pytest.raises(ProviderError, match="deaktiviert"):
        prov.analyze(AnalysisRequest(snap, UserRules()))


def test_unreachable_local_server_message(example_project):
    prov = ApiProvider("http://127.0.0.1:9/v1", "m", timeout=2)
    with pytest.raises(ProviderError, match="nicht erreichbar|Zeitüberschreitung"):
        prov.analyze(AnalysisRequest(scan_folder(example_project), UserRules()))


def test_is_local_url():
    assert is_local_url("http://localhost:1234/v1")
    assert is_local_url("http://127.0.0.1:8080/v1")
    assert is_local_url("http://192.168.1.20:11434/v1")
    assert not is_local_url("https://openrouter.ai/api/v1")


# -- Geheimnisse ----------------------------------------------------------------------

def test_secret_store_and_redaction(tmp_path):
    store = SecretStore(backend=_MemoryBackend())
    store.set("api_key:openrouter", SECRET)
    assert store.get("api_key:openrouter") == SECRET
    assert redact(f"Fehler mit {SECRET}") == "Fehler mit [ENTFERNT]"
    assert redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz") == "Authorization: [ENTFERNT]"
    store.delete("api_key:openrouter")
    assert store.get("api_key:openrouter") is None


def test_settings_never_store_api_keys(tmp_path):
    s = AppSettings()
    s.provider = "api"
    s.api.model = "x"
    s.rules.naming_style = "kebab"
    s.save_template("Meine Regeln")
    s.save()
    text = AppSettings.file_path().read_text(encoding="utf-8")
    assert "sk-" not in text and "api_key" not in text
    loaded = AppSettings.load()
    assert loaded.api.model == "x" and loaded.rules.naming_style == "kebab"
    assert loaded.load_template("Meine Regeln").naming_style == "kebab"


def test_registry():
    s = AppSettings()
    assert make_provider(s).id == "builtin"
    assert "offline" in describe_active(s)
    s.provider = "api"
    s.api.model = "meta/llama:free"
    store = SecretStore(backend=_MemoryBackend())
    store.set("api_key:openrouter", SECRET)
    prov = make_provider(s, store)
    assert prov.is_external and prov.client.api_key == SECRET
    assert "deaktiviert" in describe_active(s)
