# Review Compass — Design

Stand: 2026-11-17 · Status: Entwurf, wartet auf Freigabe

Arbeitsname `review-compass` (Vorschlag, frei änderbar).

## 1. Worum es geht und warum

Grundlage ist das Paper *Artificial Intelligence in the Firm: Bottlenecks in
Software Production* (Chen/Stratton, Harvard, Stand 14.11.2026, liegt als
`jellyfish.pdf` im Projektordner).

Kernbefund: KI-Agenten erhöhen die Zahl der Pull Requests um 35,7 %, die Zahl
erledigter Tickets aber nur um 13,7 %. Der Engpass ist Code-Review: Zeit bis
Merge +56,1 %, Wartezeit +59,9 %, aktive Review-Zeit +58,5 %, Kommentare pro
PR +38,6 %. KI-Review-Bots sind bei 79,1 % der Firmen im Einsatz, trotzdem
bekommen nur 10,8 % der PRs einen KI-Kommentar. Menschen prüfen weiter alles.

Wer allein mit Coding-Agenten arbeitet, ist selbst dieser Engpass: der Agent
schreibt, ein Mensch muss alles lesen.

Review Compass schreibt keinen weiteren Stapel Kommentare. Es beantwortet pro
PR zwei Fragen:

1. Wie genau muss ein Mensch hier hinschauen?
2. Wo zuerst?

Und es belegt mit einem Backtest auf öffentlichen Repos, wie gut die
Einstufung ist.

**Für wen:** Paul, als Studienprojekt fürs GitHub-Profil. Zusätzlich an
eigenen Repos nutzbar, in denen Agenten PRs öffnen.

## 2. Ziele, Nicht-Ziele, Anti-Ziele

### Ziele

- **Z1 Review-Brief pro PR.** Ein einziger Kommentar, der bei neuen Commits
  aktualisiert wird: Risikostufe mit Punktzahl, Gründe, Abgleich mit dem
  verlinkten Issue, Lesereihenfolge, Liste der Dateien zum Überfliegen.
- **Z2 Regeln im Repo.** Datei `.review-compass.yml`: sensible Pfade,
  Gewichte, Schwellen, Label-Namen, Auto-Freigabe an/aus.
- **Z3 Backtest.** Kommandozeile, die die PR-Historie öffentlicher Repos
  gegen dieselbe Bewertung spielt und Kennzahlen als Bericht ausgibt.
- **Z4 Einbau in fünf Minuten.** Eine Workflow-Datei pro Repo.
- **Z5 Ohne Schlüssel nutzbar.** Bewertung läuft ohne Modell-Aufruf und ohne
  Kosten. Sprachmodell ist Zusatz.

### Nicht-Ziele (erste Version)

- Gehostetes SaaS, Web-Oberfläche, Mandanten, Abrechnung
- GitLab, Azure DevOps, Bitbucket
- Zeilenweise Fehlersuche wie CodeRabbit, Greptile, Bugbot
- Reviewer-Zuweisung im Team
- Trainiertes ML-Modell. Die Bewertung ist eine offene, gewichtete Summe.
- Veröffentlichung auf PyPI

### Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- Mehr als ein Kommentar pro PR oder Text, den niemand liest. Lärm
  verschärft den Engpass.
- Punktzahl ohne nachvollziehbare Gründe.
- Auto-Freigabe, die ohne ausdrückliches Einschalten aktiv ist.
- Zahlen im README, die nicht aus einem wiederholbaren Backtest-Lauf stammen.
- Code geht an ein Sprachmodell, ohne dass der Nutzer einen Schlüssel
  hinterlegt hat.
- Sprachmodell-Ausgabe kann Risikostufe senken oder Freigabe auslösen.

## 3. Rahmenbedingungen

- Python 3.12+, `uv`, `pytest`, `ruff`, `mypy --strict`.
- Läuft als GitHub Action auf GitHub-gehosteten Runnern.
- Rechte des Workflow-Tokens: `contents: read`, `pull-requests: write`,
  `issues: read`.
- Nur PRs aus demselben Repo. Bei Fork-PRs hat das Token keine Schreibrechte:
  dann kein Kommentar, Brief landet nur in der Job-Zusammenfassung.
  `pull_request_target` wird nicht verwendet.
- Code des PRs wird nie ausgeführt, nur gelesen.
- Sprachmodell: Claude über die Anthropic-API, Modell einstellbar, Standard
  ein kleines, günstiges Modell. Modell-IDs prüft der Umsetzer gegen die
  aktuelle API-Referenz.
- Ausgabesprache des Briefs: Englisch.
- Lizenz: MIT.

## 4. Betrachtete Wege

| | Weg | Eigennutzen | Vorzeigbar | Problem |
|---|---|---|---|---|
| 1 | **Review Compass**: Risikostufe und Lesehilfe pro PR | hoch | mittel | ohne Zahlen nur "noch ein Bot" |
| 2 | **Engpass-Messung**: Paper-Kennzahlen für beliebige Repos nachrechnen | gering | mittel | löst nichts, Solo-Repos haben kaum Reviews |
| 3 | **Backtest**: Risiko-Regel gegen Historie öffentlicher Repos | keiner allein | hoch | allein kein Werkzeug |

**Gewählt von Paul: 1 mit 3 als Beleg.** Grund: 1 ist sofort an eigenen Repos
nutzbar, 3 liefert die Zahlen, die aus dem Bot ein belegtes Projekt machen.
Teile von 2 fallen ab, weil der Backtest dieselben Daten zieht.

Verworfen vorab: generischer Review-Bot und Risiko-Triage als Produkt für
Firmen. Markt ist besetzt (Cursor Approval Agents, GitHub Copilot mit
Freigabe seit Sept. 2026, Gitar, Augment, CodeRabbit). Für ein Studienprojekt
ist das unerheblich, aber es erklärt, warum der Schwerpunkt auf offenem
Beleg liegt und nicht auf Funktionsbreite.

**Form (mein Vorschlag, von Paul nicht widersprochen):** GitHub Action plus
Python-Paket mit Kommandozeile. Kein Hosting, keine laufenden Kosten außer
optionalen Modell-Aufrufen.

## 5. Was der Nutzer sieht

### Kommentar im PR

```
## Review Compass: HIGH (72/100)

Why
- touches sensitive path: auth/session.py            +25
- source changed, no test changes                    +18
- 14 files depend on changed modules                 +15
- hotspot: auth/session.py changed 9x in 90 days     +9
- authored by agent (Co-Authored-By: Claude)         +5

Intent check
Issue #31 asks for a logout fix. This PR also changes token lifetime
(auth/session.py:112). Not mentioned in the issue.

Read first
1. auth/session.py:88-140   session invalidation rewritten
2. api/login.py:12-30       caller of changed function

Skim (9 files)
renames, formatting, lockfile
```

- Genau ein Kommentar, erkannt an verstecktem Marker, wird bei neuen Commits
  ersetzt.
- Label `review:low`, `review:medium` oder `review:high`. Wird angelegt, falls
  es fehlt. Altes Label wird entfernt.
- Derselbe Text in der Job-Zusammenfassung.
- Ohne API-Schlüssel: Abschnitt "Intent check" zeigt `not run (no API key)`,
  Lesereihenfolge kommt aus der regelbasierten Datei-Rangfolge, ohne
  Begründungstext.

### Regeldatei `.review-compass.yml`

```yaml
sensitive_paths:
  - "**/auth/**"
  - "**/migrations/**"
  - ".github/workflows/**"
thresholds:
  medium: 35
  high: 60
weights:            # optional, überschreibt Standardwerte
  sensitive_path: 25
  no_tests: 18
skip_drafts: true
auto_approve: false
llm:
  enabled: true     # wirkt nur, wenn ANTHROPIC_API_KEY gesetzt ist
  model: <klein>    # Standard setzt der Umsetzer
  max_input_tokens: 30000
```

Fehlt die Datei, gelten eingebaute Standardwerte. Ungültige Datei: Job
schlägt fehl mit klarer Meldung, kein stiller Rückfall.

**Regeln kommen immer vom Basis-Branch, nie aus dem PR selbst.** Sonst
könnte ein Agent-PR Schwellen senken oder `auto_approve` einschalten und sich
selbst freigeben. Folgen:

- Regeländerungen wirken erst nach dem Merge.
- Ein PR, der `.review-compass.yml` oder den Workflow ändert, der die Action
  aufruft, gilt fest als sensibler Pfad. Das ist eingebaut und nicht
  abschaltbar. Solche PRs werden nie automatisch freigegeben.

### Auto-Freigabe (standardmäßig aus)

Nur wenn `auto_approve: true` **und** Stufe `low`: Action gibt eine
zustimmende Review ab. Entscheidung fällt ausschließlich aus der
regelbasierten Punktzahl. Bei neuem Commit wird neu bewertet; ist die Stufe
nicht mehr `low`, wird die Freigabe zurückgezogen. README nennt die nötige
Repo-Einstellung ("Allow GitHub Actions to create and approve pull requests").

### Backtest

```
review-compass backtest pallets/flask pydantic/pydantic --limit 2000
review-compass report out/ --format md
```

Ausgabe: Markdown-Bericht plus JSON mit allen Rohwerten.

## 6. Wie es arbeitet

### Signale

Alle Signale stammen aus Git-Historie und PR-Metadaten, damit Live-Betrieb
und Backtest **dieselbe** Bewertung rechnen.

| Signal | Bedeutung |
|---|---|
| Größe | geänderte Zeilen und Dateien, logarithmisch |
| Sensible Pfade | Treffer auf Muster aus der Regeldatei |
| Test-Delta | Quellcode geändert, Tests nicht |
| Abhängige | Anzahl Dateien, die geänderte Module importieren (Python, JS/TS; andere Sprachen: "not measured", 0 Punkte) |
| Hotspot | wie oft die Datei in den letzten 90 Tagen geändert wurde |
| Vertrautheit | hat der Autor diese Dateien schon früher geändert |
| Streuung | Anzahl betroffener Verzeichnisse |
| Dateiart | nur Doku, nur Konfiguration, generiert, Lockfile |
| Agent-Autor | Co-Authored-By-Zeile, Bot-Konto, Branch-Präfix |

Vertrautheit greift den Paper-Befund auf, dass 82,8 % der Reviews durch
Leute mit Vorwissen im Code erfolgen.

### Bewertung

Gewichtete Summe, auf 0–100 begrenzt. Jedes Signal liefert Punkte plus einen
Begründungssatz. Schwellen teilen in `low`, `medium`, `high`. Dieselben
Signale ergeben pro Datei eine Rangfolge für "Read first" und "Skim".

Die Bewertung ist eine reine Funktion: gleiche Eingabe, gleiche Ausgabe,
kein Netz, kein Modell.

### Sprachmodell-Teil

Eingabe: PR-Titel und -Text, Text des verlinkten Issues, Diff (gekürzt auf
Budget, riskanteste Dateien zuerst). Ausgabe als festes Schema:
Absichts-Abgleich (`matches`, `extends`, `unclear`, `no_issue` plus ein Satz)
und Begründung je Eintrag der Lesereihenfolge.

PR-Inhalt ist nicht vertrauenswürdig. Deshalb: Modell-Ausgabe füllt nur
Textfelder im Kommentar. Sie ändert weder Punktzahl noch Stufe noch
Freigabe. Reine Doku-PRs lösen keinen Modell-Aufruf aus. Fehler beim Aufruf:
Brief erscheint trotzdem, Abschnitt zeigt `not run (error)`.

### Backtest-Methode

1. Repo lokal klonen, PR-Köpfe nachladen (`refs/pull/*/head`), PR-Metadaten
   über GitHub-API ziehen, Antworten auf Platte zwischenspeichern.
2. Für jeden gemergten PR Signale zum Stand vor dem Merge berechnen. Kein
   Blick in die Zukunft.
3. Ergebnis je PR bestimmen:
   - **streng:** PR wurde innerhalb von 14 Tagen zurückgerollt (Revert-PR
     oder Commit mit "This reverts commit").
   - **weit:** zusätzlich ein Folge-PR, der die PR-Nummer nennt und als Fix,
     Hotfix oder Regression erkennbar ist.
   - **Review-Aufwand:** Zahl der Review-Kommentare, angeforderte Änderungen,
     Zeit bis Merge.
4. Kennzahlen: Anteil PRs je Stufe; Rate schlechter Ergebnisse je Stufe mit
   Konfidenzintervall; Verhältnis `high` zu `low`; AUC; Rangkorrelation
   zwischen Punktzahl und Review-Aufwand; Kernaussage "würde man `low` nur
   überfliegen: X % der PRs, darin Y von Z schlechten Ergebnissen".
5. Getrennte Auswertung für Agent-PRs, soweit erkennbar.

Der Backtest ruft nie ein Sprachmodell auf. Er prüft nur die regelbasierte
Bewertung, braucht keinen Schlüssel und kostet nichts.

Alle Backtest-Repos laufen mit **denselben eingebauten Standardregeln**. Es
gibt keine handgeschriebenen Regeln pro Repo, das wäre Anpassen an die
Testdaten. Die Kommandozeile nimmt optional eine Regeldatei (`--policy`), die
dann einheitlich für alle Repos gilt. Die eingebaute Liste sensibler Pfade
muss deshalb allgemein brauchbar sein (Authentifizierung, Migrationen,
CI-Workflows, Abhängigkeits-Manifeste, Infrastruktur). Feuert ein Signal auf
einem Repo kaum, steht das im Bericht.

**Ehrlichkeitsregeln:**

- Standardgewichte und Standardpfade werden festgelegt, **bevor** der erste
  Backtest läuft.
- Bleiben sie unverändert, reichen drei Repos, alle werden berichtet.
- Die Aufteilung gilt nur, falls nachjustiert wird: dann auf mindestens zwei
  Repos justieren und auf mindestens drei anderen berichten. README sagt das.
- README nennt die Grenzen: Reverts sind selten, nur gemergte PRs, kein
  Kausalschluss, Folge-Fix-Erkennung ist unscharf.
- Fällt der Backtest schwach aus, steht das genauso im README.

### Bausteine

| Baustein | Aufgabe | hängt ab von |
|---|---|---|
| `policy` | Regeldatei laden, prüfen, Standardwerte | nichts |
| `features` | Git-Repo + Basis/Kopf + PR-Metadaten → Signale | Git |
| `scoring` | Signale + Regeln → Punktzahl, Stufe, Gründe, Datei-Rangfolge | `policy` |
| `github` | PRs, Issues, Kommentare, Labels, Reviews; Zwischenspeicher | GitHub-API |
| `brief` | Modell-Aufruf mit festem Schema; Rückfall ohne Modell | Anthropic-API |
| `render` | Brief als Markdown | nichts |
| `backtest` | Historie, Ergebnis-Bestimmung, Kennzahlen, Bericht | `features`, `scoring`, `github` |
| `cli` | `pr`, `backtest`, `report` | alle |
| `action.yml` | zusammengesetzte Action, ruft `cli pr` | `cli` |

## 7. Tests und Abnahme

- Unit-Tests für `scoring`, `policy`, Ergebnis-Bestimmung, mit festen
  Beispiel-PRs.
- Golden-File-Test für den gerenderten Kommentar.
- `github`- und `brief`-Aufrufe gegen aufgezeichnete Antworten.
- CI: `ruff`, `mypy --strict`, `pytest` grün.

**Fertig heißt:**

1. Action läuft im eigenen Projekt-Repo. Ein echter PR bekommt Kommentar und
   Label; ein zweiter Commit ersetzt beides korrekt.
2. Lauf einmal ohne und einmal mit API-Schlüssel geprüft.
3. Backtest auf mindestens drei öffentlichen Repos gelaufen, Bericht und
   Rohdaten liegen im Repo.
4. README (Englisch) mit Einbau-Anleitung, Beispielkommentar, Backtest-Tabelle
   mit echten Zahlen, Grenzen, Bezug zum Paper.

## 8. Dem Umsetzer überlassen

- Genaue Standardgewichte und Schwellen (vor erstem Backtest festlegen)
- Import-Erkennung für Python und JS/TS im Detail
- REST oder GraphQL, Format des Zwischenspeichers
- Auswahl der Backtest-Repos. Kriterien: über 2000 gemergte PRs, Reviews
  üblich, erkennbare Revert-Praxis, Mix aus Python und TypeScript.
  Kandidaten, ungeprüft: `pallets/flask`, `pydantic/pydantic`,
  `home-assistant/core`, `vercel/next.js`, `langchain-ai/langchain`.
- Obergrenze PRs pro Repo im Backtest und Umgang mit API-Limits
- Ein Diagramm im Bericht: gewünscht, aber nachrangig

## 9. Offen bis zum Bau

- GitHub-Konto und Repo-Name, unter dem veröffentlicht wird
- Ob ein Anthropic-API-Schlüssel für den Modell-Teil bereitsteht
- Projektordner liegt auf externem Laufwerk und ist noch kein Git-Repo.
  Dieses Dokument ist deshalb noch nicht committet.
