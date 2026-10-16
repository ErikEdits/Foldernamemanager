# Architektur und technische Entscheidungen

Dieses Dokument beantwortet die offenen technischen Punkte aus Abschnitt 10 der Spezifikation.

## Überblick

```
 Ordner ──► Scanner ──► Snapshot ──► KI-Anbieter ──► Organisationsplan (JSON)
 (nur lesen)                         │ integriert                │
                                     │ lokal / GGUF              ▼
                                     │ OpenRouter/API      Planer: Zielstruktur + Konfliktprüfung
                                     │ Export/Import             │
                                                                 ▼
                                    Freigabe durch den Benutzer (Vorher/Nachher, annehmen/ablehnen/ändern)
                                                                 │
                                                                 ▼
                         Executor: Kopie ► Änderungen nur in der Kopie ► Journal ► Protokoll / Rückgängig
```

Alle KI-Varianten erzeugen dasselbe Planformat; Prüfung und Ausführung sind für alle identisch.

## Entscheidungen zu Abschnitt 10

| Offener Punkt | Entscheidung |
| --- | --- |
| Programmiersprache / UI | **Python 3.10+ mit PySide6 (Qt 6)**. Leistungsfähige Baum-/Tabellenansichten, Hintergrund-Threads, ohne Bildschirm testbar. Auslieferung als Windows-Anwendung mit PyInstaller. |
| Kostenlose lokale KI | **Integrierte Organisations-KI** (Erkennungs- und Regellogik, ohne Abhängigkeiten) als Standard. Zusätzlich lokale Sprachmodelle über OpenAI-kompatible Laufzeiten oder direkt per `llama-cpp-python`. |
| Modellformate | GGUF direkt (optional), alle Formate der jeweiligen Laufzeit (llama.cpp, LM Studio, Ollama, Jan, KoboldCpp) über deren OpenAI-kompatible Schnittstelle. |
| Sichere Speicherung von API-Keys | Windows-Anmeldeinformationsverwaltung über `keyring`; Rückfall auf Windows-DPAPI (benutzergebunden verschlüsselt); sonst nur im Arbeitsspeicher. Zusätzlich ein Schwärzungsfilter für alle ausgehenden Texte. |
| Kostenlose Modelle und Limits | Modellliste mit Preisen über `/models`; „nur kostenlose“ filtert; HTTP 429/402 werden verständlich erklärt. Die Grundfunktion hängt nicht von externen Angeboten ab. |
| Sehr große Ordnerstrukturen | Iterativer Scanner (keine Rekursionsgrenze), Fortschritt und Abbruch; Arbeit im Hintergrund-Thread; geschützte Ordner (`.git`, `node_modules` …) werden nicht analysiert. |
| Aufteilung großer Analysepakete | Pakete werden entlang der Ordnerstruktur in Teile mit einstellbarer Maximalgröße zerlegt (große Ordner rekursiv, sehr viele Dateien in Abschnitten). Teilpläne werden beim Import zusammengeführt. |
| Zuverlässige Kopie großer Verzeichnisse | Blockweises Kopieren mit Fortschritt, Abbruch und Teil-Dateien (`*.kiorg-part`), Metadaten und Windows-Attribute werden übernommen. Fortsetzen überspringt bereits vollständig kopierte Dateien (Größe + Zeitstempel). |
| Fortsetzen nach Unterbrechung | Persistentes Journal je Vorgang (`%LOCALAPPDATA%\KI-Ordnerorganisator\runs`). Beim Fortsetzen wird das Journal mit dem Dateisystem abgeglichen. |
| Symbolische Links / versteckte Dateien | Symlinks und Junctions werden **nicht verfolgt und nicht kopiert** (Hinweis). Versteckte/Systemdateien werden kopiert, aber nie umbenannt oder verschoben und standardmäßig nicht an KIs weitergegeben. |
| Berechtigungen / Windows-Pfade | Long-Path-Präfix `\\?\` für alle Dateioperationen; der echte Desktop wird über `SHGetKnownFolderPath` ermittelt (funktioniert auch mit OneDrive-Umleitung, Hinweis auf Cloud-Sync). Fehlende Rechte und gesperrte Dateien werden pro Datei gemeldet, der Vorgang läuft weiter. |
| JSON-Schema für den Plan | Siehe [plan-format.md](plan-format.md) und `ordnerorganisator/schema/organisationsplan.schema.json`. Deklarativ: Quelle (alte Struktur) → Ziel (neue Struktur), damit die Reihenfolge der Anweisungen keine Rolle spielt. |

## Umsetzung in der Kopie

1. **Vorbereitung** (`prepare_run`): Ziel `<Ordner>_Organisiert` (eindeutig), Überschneidungsprüfung, Speicherplatz
   inkl. Reserve, Schreibrechte, Cloud-Sync-Hinweis.
2. **Kopie** (`copy_phase`): vollständige 1:1-Kopie des aktuellen Ordnerinhalts.
3. **Umsetzung** (`apply_phase`): Für jede Datei der Kopie wird der neue Pfad berechnet (auch für Dateien, die nach der
   Analyse hinzugekommen sind – sie wandern mit ihrem Ordner). Verschoben wird zweistufig über einen Zwischenordner,
   sodass nie etwas überschrieben wird. Danach werden leere Zielordner angelegt, reine Groß-/Kleinschreibungsänderungen
   korrigiert und frei gewordene **leere** Ordner entfernt.
4. **Protokoll**: `protokoll.jsonl` (jedes Ereignis) und `Aenderungsprotokoll.txt` (lesbar).
5. **Rückgängig**: Alle ausgeführten Verschiebungen werden – ebenfalls zweistufig – zurückgenommen; entfernte Ordner
   werden wiederhergestellt, neu angelegte leere Ordner entfernt.

Der `PathGuard` prüft jeden Schreibzugriff: Ziele müssen innerhalb der Kopie und außerhalb des Originals liegen.
