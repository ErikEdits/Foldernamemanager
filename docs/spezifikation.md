# KI-Ordnerorganisator für Windows
## Projektspezifikation – Version 1.0

**Status:** Konzept  
**Plattform:** Windows 10/11  
**Ziel:** Sichere, intelligente und KI-gestützte Organisation von Dateien und Ordnern  
**Dokumentformat:** Markdown

---

## 1. Grundidee

Der KI-Ordnerorganisator ist ein Windows-Programm, das einen vom Benutzer ausgewählten Ordner analysiert und Vorschläge für eine bessere, logischere und übersichtlichere Ordnerstruktur erstellt.

Die KI soll:
- unpassende oder unübersichtliche Ordnernamen erkennen,
- bessere Datei- und Ordnernamen auf Deutsch oder Englisch vorschlagen,
- passende Unterordner vorschlagen,
- Dateien sinnvoller einsortieren,
- eine übersichtliche Zielstruktur erstellen.

### Wichtigstes Sicherheitsprinzip

**Das Original darf niemals verändert oder gelöscht werden.**

Erst nachdem der Benutzer die Vorschläge geprüft und bestätigt hat, erstellt das Programm eine Kopie des ausgewählten Ordners auf dem Desktop. Ausschließlich diese Kopie wird anschließend organisiert.

---

## 2. Geplanter Programmablauf

### Schritt 1: Ordner auswählen

Beim Start kann der Benutzer:
- einen vollständigen Ordnerpfad eingeben,
- einen Ordner über einen Dateidialog auswählen.

### Schritt 2: Ordnerstruktur anzeigen

Das Programm zeigt die bestehende Struktur übersichtlich an.

Angezeigt werden:
- alle Ordner und Unterordner,
- alle enthaltenen Dateien,
- Datei- und Ordnernamen,
- vollständige oder relative Pfade,
- Dateiendungen und Dateitypen.

Die Struktur soll übersichtlich, durchsuchbar und leicht verständlich sein.

### Schritt 3: KI-Analyse

Die KI untersucht die verfügbaren Informationen und erstellt Vorschläge.

Beispiele:
- unklare Ordnernamen verbessern,
- einheitliche Namensregeln vorschlagen,
- passende Unterordner erstellen,
- Dateien thematisch sinnvoll gruppieren,
- eine bessere Gesamtstruktur entwerfen.

Die KI darf nicht einfach behaupten, den Inhalt einer Datei zu kennen, wenn sie nur deren Dateinamen sieht. Bei Unsicherheit soll sie nachfragen oder den bisherigen Namen beibehalten.

### Schritt 4: Vorschläge überprüfen

Vor der Ausführung sieht der Benutzer:
- die alte Ordnerstruktur,
- die vorgeschlagene neue Struktur,
- geplante Umbenennungen,
- geplante Verschiebungen,
- neu anzulegende Ordner.

Der Benutzer kann einzelne Vorschläge akzeptieren, ablehnen oder ändern.

### Schritt 5: Kopie erstellen

Nach ausdrücklicher Bestätigung:
1. prüft das Programm die geplanten Änderungen,
2. kontrolliert es den verfügbaren Speicherplatz,
3. erstellt es eine Kopie des ausgewählten Ordners auf dem Desktop,
4. setzt es die bestätigten Änderungen ausschließlich in dieser Kopie um,
5. zeigt es den Fortschritt und das Ergebnis an.

Das Original bleibt unverändert.

---

## 3. Eigenständige KI und KI-Anbieter

### 3.1 Möglichst unabhängige KI-Lösung

Das Programm soll nicht zwingend von Ollama oder einem bestimmten externen Open-Source-Modell abhängig sein.

Gewünscht ist eine eigene, auf die Ordnerorganisation spezialisierte KI-Lösung beziehungsweise eine integrierte KI-Komponente.

Optional sollen andere KI-Modelle und Anbieter eingebunden werden können.

Mögliche technische Wege:
- eine eigens entwickelte KI-Komponente,
- eine lokal eingebundene Modelldatei,
- eine kompatible lokale Modell-Laufzeit,
- optionale externe KI-Dienste.

**Technischer Hinweis:** Ein leistungsfähiges Sprachmodell vollständig von Grund auf zu entwickeln und zu trainieren, ist ein sehr großes Vorhaben. Die Anwendung kann jedoch eigene Analyse-, Regel- und Organisationslogik enthalten und eine passende lokale KI integrieren. Die genaue Architektur wird später festgelegt.

### 3.2 Kostenlose Standardlösung

Die Standardlösung soll zu 100 % kostenlos nutzbar sein.

Anforderungen:
- keine verpflichtenden kostenpflichtigen API-Aufrufe,
- keine versteckten Gebühren,
- keine zwingende Registrierung bei einem Cloud-Anbieter,
- möglichst lokale Verarbeitung ohne Internetverbindung.

Ein externer kostenloser Dienst kann Nutzungsgrenzen oder Verfügbarkeitsbeschränkungen haben. Deshalb darf die Grundfunktion nicht von einem dauerhaft kostenlosen Cloud-Angebot abhängig sein.

### 3.3 Optionale API-Anbindung

Der Benutzer kann optional einen API-Key eintragen.

**OpenRouter ist als möglicher Anbieter ausdrücklich vorgesehen.**

Weitere Anforderungen:
- kompatible Anbieter später ergänzen können,
- verfügbare Modelle nach Möglichkeit auswählen können,
- aktiven Anbieter und Modell anzeigen,
- mögliche Kosten und Nutzungslimits transparent anzeigen,
- die externe KI jederzeit deaktivieren können.

API-Keys müssen sicher gespeichert werden. Sie dürfen niemals in Analysepakete, Protokolle oder exportierte Dateien gelangen.

---

## 4. Welche Informationen die KI erhält

Für die normale Strukturanalyse soll das Programm folgende Informationen erfassen können:

- Namen sämtlicher Ordner,
- Namen aller Unterordner,
- Namen aller Dateien,
- relative und gegebenenfalls vollständige Pfade,
- Verschachtelung der Ordner,
- Dateiendungen und Dateitypen,
- Beziehungen zwischen Ordnern und Dateien.

Die KI soll anhand dieser Informationen Vorschläge erstellen.

### Optionale Analyse von Dateiinhalten

Eine zusätzliche, separat aktivierbare Funktion darf ausgewählte Dateiinhalte untersuchen, wenn die Namen allein nicht ausreichen.

Dabei muss klar angezeigt werden:
- welche Dateien analysiert werden,
- welche Informationen verwendet werden,
- ob Inhalte an eine externe KI übertragen werden.

Standardmäßig sollen keine Dateiinhalte oder Zugangsdaten exportiert werden.

---

## 5. Intelligente Namensgebung und Struktur

Die KI soll folgende Verbesserungen vorschlagen können:

- verständlichere Ordnernamen,
- aussagekräftigere Dateinamen,
- einheitliche Schreibweisen,
- deutsche oder englische Namen,
- sinnvolle Unterordner,
- eine nachvollziehbare Gruppierung,
- passende Namenskonventionen.

### Beispiel

Vorher:

    MeinProjekt/
    ├── Downloads neu/
    ├── datei1.txt
    ├── Bilder/
    └── final_final_v2.pdf

Möglicher Vorschlag:

    MeinProjekt_Organized/
    ├── Documents/
    │   ├── Project_Notes.txt
    │   └── Final_Document.pdf
    └── Images/

Dies ist nur ein Beispiel. Die KI muss bei tatsächlichen Dateien vorsichtig vorgehen und darf keine unbegründeten Zuordnungen vornehmen.

---

## 6. Cloud-KI-Übergabe über Export und Import

Das Programm soll einen Modus bieten, bei dem der Benutzer Informationen an ChatGPT oder eine andere Cloud-KI übergeben kann, ohne einen API-Key im Programm einzurichten.

### 6.1 Analysepaket erstellen

Das Programm erstellt ein Paket mit allen für die Aufgabe relevanten Informationen.

Es kann enthalten:
- die Ordnerstruktur,
- Datei- und Ordnernamen,
- relevante Pfade und Dateitypen,
- die Aufgabenstellung,
- die Regeln für die Organisation,
- das erwartete Rückgabeformat.

Der Benutzer kann zwischen zwei Möglichkeiten wählen.

**Option A: In die Zwischenablage kopieren**

Das Programm kopiert den vorbereiteten Text in die Zwischenablage. Der Benutzer fügt ihn in ChatGPT oder eine andere KI ein.

**Option B: Datei auf dem Desktop speichern**

Das Programm erstellt eine Datei, die der Benutzer bei einer Cloud-KI hochladen kann.

Das Paket soll eine geeignete Anweisung enthalten, damit die KI weiß, dass sie einen strukturierten Organisationsplan zurückgeben muss.

### 6.2 Antwort importieren

Die Cloud-KI erstellt einen strukturierten Plan. Der Benutzer speichert diesen als Datei und importiert ihn wieder in den Ordnerorganisator.

Als Austauschformat wird JSON empfohlen.

Der Plan kann beispielsweise enthalten:
- neue Ordner,
- neue Ordnernamen,
- Umbenennungen,
- Verschiebungen,
- Begründungen,
- Warnungen bei unsicheren Entscheidungen.

### 6.3 Plan validieren und ausführen

Nach dem Import:
1. prüft das Programm die Dateistruktur und das Format,
2. kontrolliert es die Pfade und möglichen Konflikte,
3. weist es ungültige oder widersprüchliche Anweisungen zurück,
4. zeigt es eine Vorschau der geplanten Änderungen,
5. wartet es auf die ausdrückliche Bestätigung,
6. erstellt es die Kopie und setzt nur die genehmigten Änderungen um.

**Die Cloud-KI liefert ausschließlich einen Plan. Das lokale Programm führt die Dateioperationen aus.**

Importierte Anweisungen dürfen keine Dateien außerhalb der Arbeitskopie verändern.

### 6.4 Datenschutz

Vor dem Export soll der Benutzer sehen können, welche Informationen weitergegeben werden.

Dateinamen und Pfade können persönliche Informationen enthalten. Daher soll der Benutzer das Paket vor der Weitergabe prüfen können.

Passwörter, API-Keys und andere Geheimnisse dürfen niemals Bestandteil des Pakets sein.

---

## 7. Sicherheit und Zuverlässigkeit

### Original schützen

Das Originalverzeichnis wird niemals durch die Organisationsfunktion verändert.

### Keine automatischen Löschungen

Dateien werden nicht gelöscht, auch nicht im Rahmen einer vermeintlichen Bereinigung.

### Bestätigung vor Änderungen

Die Anwendung darf die genehmigten Dateioperationen erst nach ausdrücklicher Zustimmung durchführen.

### Konflikte erkennen

Vor der Umsetzung prüft das Programm:
- doppelte Zielnamen,
- ungültige Dateinamen,
- ungültige oder unerlaubte Pfade,
- Konflikte beim Verschieben,
- fehlende Dateien.

### Speicherplatz prüfen

Vor dem Kopieren wird kontrolliert, ob voraussichtlich ausreichend Speicherplatz vorhanden ist.

### Fehler verständlich melden

Beispiele:
- Datei ist gesperrt,
- Berechtigung fehlt,
- Speicherplatz reicht nicht,
- Datei kann nicht kopiert werden,
- Zielname ist bereits vorhanden.

### Änderungsprotokoll

Das Programm protokolliert:
- geplante Änderungen,
- genehmigte Änderungen,
- erfolgreich ausgeführte Aktionen,
- fehlgeschlagene Aktionen.

### Rückgängig-Funktion

Änderungen sollen innerhalb der erzeugten Kopie soweit technisch möglich rückgängig gemacht werden können.

### Unterbrechungen

Lange Vorgänge sollen nach einem Abbruch möglichst fortgesetzt werden können.

---

## 8. Optionale Erweiterungen

Die folgenden Funktionen sind Vorschläge für die weitere Planung.

### 8.1 Visueller Vorher-nachher-Vergleich

Alte und neue Struktur werden nebeneinander angezeigt.

Geplante Umbenennungen, Verschiebungen und neue Ordner werden markiert.

### 8.2 Spezialmodi für Projekttypen

Das Programm kann passende Organisationsweisen für verschiedene Projektarten anbieten, zum Beispiel:
- Java-Projekte,
- Minecraft-Plugins,
- Videoprojekte,
- Fotosammlungen,
- allgemeine Dateiablagen.

Die Projektart kann automatisch vorgeschlagen oder manuell ausgewählt werden.

### 8.3 Eigene Regeln und Vorlagen

Der Benutzer kann eigene Regeln festlegen:
- bevorzugte Sprache,
- Namenskonventionen,
- Datumsformate,
- gewünschte Ordnernamen,
- bevorzugte Projektstrukturen.

### 8.4 Sicherheits- und Speicherplatzprüfung

Vor dem Kopieren werden mögliche Probleme erkannt und erklärt.

### 8.5 Vorgang fortsetzen

Ein unterbrochener Analyse- oder Kopiervorgang soll möglichst fortsetzbar sein.

### 8.6 Optionale Inhaltsanalyse

Der Benutzer kann ausgewählte Dateien zusätzlich analysieren lassen, sofern er diese Funktion ausdrücklich aktiviert.

### Ausdrücklich nicht vorgesehen

Eine Duplikat-Erkennungsfunktion soll nicht Teil des Projekts sein. Dieser Vorschlag wurde abgelehnt.

---

## 9. Grobe Benutzeroberfläche

Die Oberfläche soll die Arbeit in klaren Schritten darstellen.

1. **Startseite:** Ordnerpfad eingeben oder Ordner auswählen.
2. **Strukturansicht:** Bestehende Ordner und Dateien untersuchen.
3. **KI-Auswahl:** Lokale Standardlösung, API-Anbindung oder Cloud-Export/Import auswählen.
4. **Analyseergebnis:** Vorschläge und Begründungen lesen.
5. **Vergleich und Freigabe:** Änderungen prüfen und auswählen.
6. **Ausführung:** Kopie erstellen und Fortschritt anzeigen.
7. **Abschluss:** Speicherort und Änderungsprotokoll anzeigen.

---

## 10. Offene technische Entscheidungen

Vor beziehungsweise während der Entwicklung müssen folgende Punkte geklärt werden:

- Programmiersprache und Windows-UI-Framework,
- technische Umsetzung der kostenlosen lokalen KI,
- Unterstützung verschiedener Modellformate,
- sichere Speicherung von API-Keys,
- Verfügbarkeit und Limits kostenloser Modelle,
- Umgang mit sehr großen Ordnerstrukturen,
- Aufteilung großer Analysepakete,
- zuverlässige Kopie großer Verzeichnisse,
- Umgang mit symbolischen Links und versteckten Dateien,
- Berechtigungen und spezielle Windows-Pfade,
- genaues JSON-Schema für den Organisationsplan.

---

## 11. Zusammenfassung

Der KI-Ordnerorganisator ist eine Windows-Anwendung, die bestehende Ordnerstrukturen analysiert, Verbesserungen vorschlägt und eine organisierte Kopie erstellt, ohne das Original zu verändern.

Er soll eine kostenlose Standardlösung, optionale API-Anbindungen wie OpenRouter und einen manuellen Austausch mit Cloud-KIs wie ChatGPT unterstützen.

Die KI erstellt Vorschläge oder strukturierte Pläne. Das Programm prüft diese und führt Änderungen erst nach ausdrücklicher Zustimmung in der Kopie aus.

Das zentrale Ziel ist eine **sichere, flexible und nachvollziehbare Datei- und Ordnerorganisation mit KI-Unterstützung**.
