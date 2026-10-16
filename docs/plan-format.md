# Austauschformat: Organisationsplan (JSON, Version 1)

Der Organisationsplan ist die einzige Schnittstelle zwischen einer KI (integriert, lokal, API oder Cloud-KI per
Export/Import) und dem Programm. **Die KI liefert nur den Plan – alle Dateioperationen führt das Programm selbst aus,
nach Prüfung und Freigabe und ausschließlich in der Kopie.**

JSON-Schema: [`ordnerorganisator/schema/organisationsplan.schema.json`](../ordnerorganisator/schema/organisationsplan.schema.json)

## Beispiel

```json
{
  "format": "ki-ordnerorganisator-plan",
  "version": 1,
  "package_id": "3f9c0a1b2c4d",
  "summary": "Dokumente gesammelt, Schreibweise vereinheitlicht.",
  "operations": [
    {"id": "op1", "type": "create_folder", "target": "Dokumente/Archiv", "reason": "Platz für ältere Dokumente"},
    {"id": "op2", "type": "rename", "source": "Downloads neu", "target": "Downloads_Neu",
     "reason": "Einheitliche Schreibweise", "confidence": 0.9},
    {"id": "op3", "type": "move", "source": "final_final_v2.pdf", "target": "Dokumente/Final_v2.pdf",
     "reason": "Dokument einsortiert, doppeltes „final“ entfernt", "confidence": 0.8}
  ],
  "warnings": ["…"],
  "questions": ["Was enthält „datei1.txt“?"]
}
```

## Felder

| Feld | Pflicht | Bedeutung |
| --- | --- | --- |
| `operations` | ja | Liste der Anweisungen |
| `format`, `version` | nein | Formatkennung (`ki-ordnerorganisator-plan`, `1`) |
| `package_id` | nein | aus dem Analysepaket übernehmen; Abweichungen werden als Hinweis angezeigt |
| `summary` | nein | Zusammenfassung |
| `warnings` | nein | Hinweise auf Risiken |
| `questions` | nein | Rückfragen bei Unsicherheit |
| `project_type`, `language` | nein | `general`/`java`/`minecraft`/`video`/`photos`, `de`/`en` |

### Operationen

| Feld | Bedeutung |
| --- | --- |
| `type` | `create_folder`, `rename` oder `move` |
| `source` | Pfad in der **ursprünglichen** Struktur (nicht bei `create_folder`) |
| `target` | vollständiger Pfad in der **neuen** Struktur |
| `reason` | Begründung |
| `confidence` | Sicherheit 0–1 (unter 0,5 wird der Vorschlag nicht vorausgewählt) |

## Semantik

- Pfade sind relativ zum gewählten Ordner, mit `/` getrennt, ohne Laufwerk, ohne führendes `/`, ohne `..`.
- `source` bezieht sich immer auf die ursprüngliche Struktur – auch wenn ein übergeordneter Ordner gleichzeitig
  umbenannt wird. `target` bezieht sich auf die neue Struktur.
- Wird ein Ordner umbenannt oder verschoben, wandert sein gesamter Inhalt mit. Eigene Anweisungen für Inhalte
  überschreiben das (z. B. eine Datei aus dem umbenannten Ordner herausnehmen).
- Elternordner von Zielen entstehen automatisch; `create_folder` ist nur für zusätzliche (leere) Ordner nötig.
- Zwei Ordner mit demselben Ziel werden zusammengeführt (Hinweis). Zwei **Dateien** mit demselben Ziel sind ein Fehler.
- `rename` mit einem bloßen Namen als `target` (ohne `/`) benennt im bisherigen Ordner um.
- Eine reine Änderung der Groß-/Kleinschreibung ist erlaubt.

## Toleranz beim Einlesen

Damit Antworten aus Chat-Oberflächen direkt funktionieren, akzeptiert der Import auch:

- JSON in Markdown-Codeblöcken mit Begleittext, UTF-8 mit BOM,
- alternative Feldnamen (`from`/`to`, `action`, `new_name`, `path`) und Typen (`mkdir`, `verschieben`, `umbenennen` …),
- Sicherheit als Prozentwert (`80` → `0.8`),
- mehrere Teilpläne (große Ordner), die zusammengeführt werden.

## Was zurückgewiesen wird

Jede Anweisung wird gegen den aktuellen Ordner geprüft. Zurückgewiesen (mit Erklärung) werden u. a.:

- verbotene Operationen (`delete`, `remove`, `overwrite` …),
- absolute Pfade, `..`, Laufwerksbuchstaben, UNC-Pfade,
- unzulässige Windows-Namen (`<>:"/\|?*`, Steuerzeichen, `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`,
  Punkt oder Leerzeichen am Ende, über 255 Zeichen),
- nicht existierende Quellen, Verknüpfungen als Quelle, mehrere Anweisungen für dieselbe Quelle,
- doppelte Ziele (ohne Beachtung der Groß-/Kleinschreibung), Datei-/Ordner-Konflikte, Ziele innerhalb einer Datei,
- Ordner, die in sich selbst verschoben würden.

Hinweise (keine Fehler): geänderte Dateiendungen, zusammengeführte Ordner, Pfade über 260 Zeichen, Anweisungen in
geschützten Ordnern, Ziele in Ordnern, die gleichzeitig umbenannt werden.
