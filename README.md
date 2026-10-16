# KI-Ordnerorganisator für Windows

Sichere, intelligente und KI-gestützte Organisation von Dateien und Ordnern unter Windows 10/11.

Der KI-Ordnerorganisator analysiert einen Ordner, schlägt bessere Namen und eine übersichtlichere Struktur vor
und setzt die Vorschläge **erst nach Ihrer Freigabe** um – und zwar **ausschließlich in einer Kopie auf dem
Desktop**.

> **Wichtigstes Sicherheitsprinzip:** Das Original wird niemals verändert oder gelöscht.
> Es wird nur gelesen. Jede schreibende Dateioperation wird technisch auf die Arbeitskopie beschränkt.

---

## Inhalt

- [Funktionen](#funktionen)
- [Installation und Start](#installation-und-start)
- [Ablauf in 7 Schritten](#ablauf-in-7-schritten)
- [KI-Optionen](#ki-optionen)
- [Cloud-KI per Export/Import (ChatGPT & Co.)](#cloud-ki-per-exportimport-chatgpt--co)
- [Sicherheit und Datenschutz](#sicherheit-und-datenschutz)
- [Kommandozeile](#kommandozeile)
- [Projektstruktur und Architektur](#projektstruktur-und-architektur)
- [Entwicklung, Tests und Windows-Build](#entwicklung-tests-und-windows-build)
- [Grenzen und Hinweise](#grenzen-und-hinweise)

---

## Funktionen

| Bereich | Umsetzung |
| --- | --- |
| Ordner auswählen | Pfad eingeben (mit Verlauf) oder per Dialog wählen |
| Strukturansicht | Durchsuchbarer Baum mit Namen, Typen, Größen und relativen Pfaden; versteckte/Systemdateien ein-/ausblendbar; erkannte Projektart |
| KI-Analyse | **Integrierte KI** (kostenlos, offline), **lokales Sprachmodell**, **API-Anbieter wie OpenRouter** oder **Cloud-KI per Export/Import** |
| Vorschläge prüfen | Vorher-/Nachher-Bäume nebeneinander, farbig markierte Änderungen, einzelne Vorschläge annehmen, ablehnen oder Zielnamen ändern, eigene Änderungen ergänzen |
| Kopie erstellen | Prüfung von Konflikten, Pfaden und Speicherplatz → Kopie auf dem Desktop → Änderungen nur in der Kopie → Fortschritt und Ergebnis |
| Änderungsprotokoll | geplante, genehmigte, ausgeführte und fehlgeschlagene Aktionen (lesbare Textdatei + maschinenlesbares Journal) |
| Rückgängig | stellt in der Kopie die ursprüngliche Struktur wieder her |
| Fortsetzen | unterbrochene Kopier- und Umsetzvorgänge lassen sich fortsetzen – auch nach einem Neustart |
| Spezialmodi | allgemeine Dateiablage, Java-Projekte, Minecraft-Plugins, Videoprojekte, Fotosammlungen (automatisch erkannt oder manuell gewählt) |
| Eigene Regeln & Vorlagen | Sprache (Deutsch/Englisch/automatisch), Namenskonvention, Datumsformat, bevorzugte Ordnernamen, freie Regeln; als Vorlagen speicherbar |
| Optionale Inhaltsanalyse | nur für ausdrücklich ausgewählte Dateien (Text, Office, PDF-Titel, Java-Paket) – mit Anzeige, wohin die Auszüge gehen |

Bewusst **nicht** enthalten: eine Duplikat-Erkennung (laut Spezifikation abgelehnt) und jede Form von Löschfunktion.

## Installation und Start

### Fertige Windows-Anwendung

Die GitHub-Actions-Pipeline baut bei jedem Push eine Windows-Version (Artefakt **KI-Ordnerorganisator-Windows**).
Entpacken und `KI-Ordnerorganisator.exe` starten. Im selben Ordner liegt `ki-ordnerorganisator-cli.exe` für die
Kommandozeile.

### Aus dem Quellcode (Python 3.10 oder neuer)

```powershell
git clone https://github.com/ErikEdits/Foldernamemanager.git
cd Foldernamemanager
python -m pip install -r requirements.txt
python -m ordnerorganisator          # startet die Oberfläche
```

Optional für Modelldateien (.gguf) direkt im Programm: `python -m pip install llama-cpp-python`.

## Ablauf in 7 Schritten

1. **Ordner auswählen** – Pfad eingeben oder „Durchsuchen …“. Unterbrochene Vorgänge werden hier zum Fortsetzen
   angeboten.
2. **Struktur ansehen** – alle Ordner und Dateien mit Typ, Größe und Pfad; Suchfeld; erkannte Projektart.
3. **KI-Auswahl und Regeln** – KI-Lösung wählen, Sprache, Namenskonvention, Datumsformat, Projektart, eigene
   Regeln, Vorlagen, optionale Inhaltsanalyse und Speicherort der Kopie festlegen.
4. **Analyseergebnis** – Zusammenfassung, Vorschläge mit Begründung und Sicherheit, Hinweise und Rückfragen der KI.
   Ungültige Anweisungen werden hier bereits zurückgewiesen und erklärt.
5. **Vergleich und Freigabe** – links das Original, rechts die Vorschau der Kopie. Vorschläge per Haken annehmen
   oder ablehnen, Zielpfade per Doppelklick ändern, eigene Änderungen über das Kontextmenü hinzufügen. Jede Änderung
   wird sofort geprüft; bei Konflikten ist „Kopie erstellen“ gesperrt.
6. **Ausführung** – nach ausdrücklicher Bestätigung (mit Anzeige von Ziel, Dateianzahl, benötigtem und freiem
   Speicher) wird zuerst die vollständige Kopie erstellt, danach werden die genehmigten Änderungen darin umgesetzt.
   Abbrechen ist jederzeit möglich; der Stand bleibt gespeichert.
7. **Abschluss** – Speicherort der Kopie, Änderungsprotokoll, aufgetretene Probleme; „Kopie im Explorer öffnen“ und
   „Änderungen in der Kopie rückgängig machen“.

Vorschläge mit geringer Sicherheit (unter 50 %) werden angezeigt, aber **nicht vorausgewählt**.

## KI-Optionen

### 1. Integrierte KI (Standard – kostenlos, offline)

Eine eigene, auf Ordnerorganisation spezialisierte Komponente ohne externe Abhängigkeiten, ohne Internet und ohne
Registrierung. Sie arbeitet mit Erkennungs- und Regellogik:

- **Projektart erkennen** (Build-Dateien, `plugin.yml`, Schnittprojekte, Bildanteil …) und Software-Projekte
  (z. B. mit `package.json`, `.git`, `pom.xml`) **schützen** – Build-Werkzeuge erwarten feste Pfade.
- **Namen vereinheitlichen**: Leerzeichen, doppelte Wörter (`final_final_v2.pdf` → `Final_v2.pdf`),
  Kopie-Kennzeichnungen (`Rechnung (1).PDF` → `Rechnung.pdf`, nur wenn das „Original“ nicht existiert),
  Datumsangaben (`12.03.2024` → `2024-03-12`), Dateiendungen, Namenskonvention.
- **Sprache**: Deutsch oder Englisch; „automatisch“ übernimmt die Sprache der Mehrheit vorhandener Namen.
  Übersetzt werden nur Namen, deren Wörter vollständig bekannt sind (keine Mischnamen).
- **Gruppieren** nach Dateityp, wobei vorhandene Sammelordner (z. B. `Bilder`, `Images`) bevorzugt werden und
  Ordner mit eindeutig dominierendem Typ (z. B. Fotoalbum) nicht zerlegt werden.
- **Spezialmodi**: Video (nummerierte Standardstruktur, Hinweis auf Neuverknüpfung), Fotos (Jahr/Monat aus dem
  Dateinamen, Begleitdateien wie `.xmp` wandern mit), Java/Minecraft (`docs/`, `src/main/resources/plugin.yml`,
  Paketpfad nur mit Inhaltsanalyse).
- **Ehrlichkeit statt Raten**: Nichtssagende Namen wie `datei1.txt` werden **nicht** umbenannt; stattdessen stellt
  die KI eine Rückfrage. Mit aktivierter Inhaltsanalyse kann ein Name aus der Überschrift abgeleitet werden
  (Sicherheit 50 %, mit Begründung).

### 2. Lokales Sprachmodell (kostenlos, offline)

- **Lokale Laufzeit mit OpenAI-kompatibler Schnittstelle**, z. B. llama.cpp-Server, LM Studio, Ollama, Jan oder
  KoboldCpp. Voreinstellungen für die üblichen Adressen sind enthalten; „Modelle laden“ fragt die verfügbaren Modelle ab.
- **Modelldatei (.gguf) direkt laden** – benötigt das optionale Paket `llama-cpp-python`.

### 3. API-Anbieter (z. B. OpenRouter)

- OpenRouter ist vorkonfiguriert; weitere OpenAI-kompatible Anbieter lassen sich über die Adresse eintragen.
- „Modelle laden“ zeigt verfügbare Modelle mit **Preisen** (bzw. „kostenlos – Nutzungslimits des Anbieters“);
  „nur kostenlose“ filtert die Liste.
- Vor dem Senden werden **Umfang (Tokens), geschätzte Kosten** und **was übertragen wird** angezeigt. Nach der
  Analyse werden die tatsächlich gemeldeten Tokens/Kosten angezeigt.
- Der Hauptschalter **„Externe KI erlauben“** ist standardmäßig aus; ohne ihn findet keine Übertragung statt.
- Aktiver Anbieter und Modell stehen jederzeit in der Statusleiste.

### 4. Cloud-KI per Export/Import

Siehe nächster Abschnitt – funktioniert ohne API-Key.

Alle KI-Varianten liefern **nur einen Plan**. Das Programm prüft ihn und führt die Dateioperationen selbst aus.

## Cloud-KI per Export/Import (ChatGPT & Co.)

1. **Analysepaket erstellen**: „In Zwischenablage kopieren“ oder „Als Datei auf dem Desktop speichern“.
   Das Paket enthält Aufgabe, Regeln, Ihre Vorgaben, das erwartete JSON-Format und die Ordnerstruktur.
   Mit „Vorschau prüfen“ sehen Sie vorher genau, was weitergegeben wird.
2. Text in ChatGPT einfügen bzw. Datei hochladen. Die KI antwortet mit einem JSON-Plan.
3. **Antwort importieren**: „Antwort einfügen …“ (begleitender Text und Codeblöcke werden automatisch erkannt) oder
   „Antwort-Datei(en) öffnen …“. Große Ordner werden in mehrere Teile aufgeteilt; die Teilantworten können gemeinsam
   importiert werden.
4. Der Plan wird geprüft: ungültige oder widersprüchliche Anweisungen (z. B. Löschen, `..`, absolute Pfade,
   unzulässige Windows-Namen, doppelte Ziele, fehlende Quellen) werden **zurückgewiesen und erklärt**.

Das Austauschformat ist in [`docs/plan-format.md`](docs/plan-format.md) beschrieben; das JSON-Schema liegt unter
[`ordnerorganisator/schema/organisationsplan.schema.json`](ordnerorganisator/schema/organisationsplan.schema.json).

## Sicherheit und Datenschutz

- **Original schützen**: Der Quellordner wird nur gelesen. Ein Pfadwächter (`PathGuard`) bricht jede Schreiboperation
  ab, die außerhalb der Kopie oder innerhalb des Originals landen würde. Liegt das Ziel im gewählten Ordner (z. B. weil
  der Benutzerordner gewählt wurde), wird die Kopie verweigert.
- **Keine Löschungen**: Es gibt keine Löschoperation. Entfernt werden nur leere Ordner, die beim Umbenennen in der Kopie
  frei werden, und eigene unvollständige Teilkopien. Nichts wird überschrieben – Kollisionen werden ausgewichen und
  protokolliert. Verschiebungen laufen zweistufig über einen Zwischenordner (auch Namenstausch `a ↔ b` ist sicher).
- **Prüfungen vor der Umsetzung**: doppelte Zielnamen (ohne Groß-/Kleinschreibung, wie Windows), ungültige Namen
  (`<>:"/\|?*`, `CON`, `NUL`, `COM1` …, Punkt/Leerzeichen am Ende), unerlaubte Pfade, Datei-/Ordner-Konflikte,
  Ordner-in-sich-selbst, fehlende Dateien, Pfade über 260 Zeichen, Speicherplatz, Schreibrechte, Cloud-Sync-Ordner.
- **Verständliche Fehler**: gesperrte Dateien, fehlende Berechtigung, Speicher voll, zu lange Pfade usw. werden auf
  Deutsch erklärt; nicht kopierbare Dateien können erneut versucht oder übersprungen werden.
- **API-Keys**: gespeichert in der Windows-Anmeldeinformationsverwaltung (über `keyring`), alternativ per Windows-DPAPI
  verschlüsselt, sonst nur für die Sitzung. Keys gelangen nie in Einstellungen, Pakete, Protokolle oder Exporte –
  ein Filter entfernt bekannte Schlüssel und typische Token-Muster zusätzlich aus allen ausgehenden Texten.
- **Datenschutz beim Export**: standardmäßig nur relative Pfade (kein Benutzername), keine versteckten/Systemdateien,
  keine Dateiinhalte. Geschützte Ordner (`.git`, `node_modules` …) werden nur zusammengefasst.

## Kommandozeile

```powershell
python -m ordnerorganisator scan       "C:\Users\Name\Documents\MeinProjekt"
python -m ordnerorganisator analyse    "C:\…\MeinProjekt" -o plan.json        # integrierte KI
python -m ordnerorganisator export     "C:\…\MeinProjekt"                     # Analysepaket auf den Desktop
python -m ordnerorganisator pruefen    "C:\…\MeinProjekt" antwort.json        # Plan prüfen, neue Struktur zeigen
python -m ordnerorganisator ausfuehren "C:\…\MeinProjekt" antwort.json        # mit Rückfrage
python -m ordnerorganisator vorgaenge
python -m ordnerorganisator fortsetzen   <Vorgangs-ID>
python -m ordnerorganisator rueckgaengig <Vorgangs-ID>
```

## Projektstruktur und Architektur

```
ordnerorganisator/
├── scanner.py          Struktur rein lesend erfassen (Symlinks/Junctions werden nicht verfolgt)
├── models.py           Snapshot-Datenmodell, geschützte Ordner/Dateien
├── windows_names.py    Windows-Namensregeln, sichere relative Pfade
├── plan.py             Organisationsplan + tolerantes JSON-Einlesen
├── planner.py          Zielstruktur berechnen, Konflikte erkennen
├── executor.py         Kopie, Umsetzung, Journal, Fortsetzen, Rückgängig, Protokoll
├── content.py          optionale Inhaltsanalyse
├── settings.py         Einstellungen, Regeln, Vorlagen (ohne Geheimnisse)
├── secrets_store.py    sichere Ablage von API-Keys
├── redaction.py        Schutz vor Geheimnissen in Texten
├── ai/                 integrierte KI, Namensregeln, Projektarten, Pakete, OpenRouter/lokal, GGUF
├── gui/                PySide6-Oberfläche (7-Schritte-Assistent)
├── cli.py              Kommandozeile
└── schema/             JSON-Schema des Organisationsplans
```

Die Entscheidungen zu den offenen technischen Punkten der Spezifikation (Abschnitt 10) sind in
[`docs/architektur.md`](docs/architektur.md) dokumentiert.

## Entwicklung, Tests und Windows-Build

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest            # Kernlogik, KI, Anbieter (mit lokalem Testserver), CLI und Oberfläche (headless)
.\packaging\build_windows.ps1   # erzeugt dist\KI-Ordnerorganisator\KI-Ordnerorganisator.exe
```

Die CI (`.github/workflows/ci.yml`) testet unter Windows und Linux und baut die Windows-Anwendung.

## Grenzen und Hinweise

- Die integrierte KI ist eine spezialisierte Erkennungs- und Regel-Engine, kein großes Sprachmodell. Ein solches von
  Grund auf zu trainieren, wäre ein eigenes Großprojekt; für freiere Vorschläge lassen sich lokale Modelle oder
  externe Anbieter einbinden – alle Pläne durchlaufen dieselben Prüfungen.
- Kostenlose Modelle externer Anbieter haben Nutzungsgrenzen und können sich ändern; die Grundfunktion hängt davon
  nicht ab.
- Videoschnitt- und andere Projektdateien speichern Medienpfade. Nach dem Verschieben in der Kopie müssen Medien dort
  ggf. neu verknüpft werden (Hinweis erscheint im Analyseergebnis).
- Symbolische Links und Verzeichnisverbindungen werden nicht verfolgt und nicht kopiert (Hinweis vor dem Kopieren).
