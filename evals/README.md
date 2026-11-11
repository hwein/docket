# Vergleichstest

Misst, wie sich drei Stände in fünf Fällen verhalten (REWORK-SPEC.md, Schritt 0): `none` (ohne Plugin), `0.5.11`
(Export `git archive c7cf2b7` nach `%TEMP%/docket-evals/plugin-0.5.11-c7cf2b7`, von `run.py` angelegt, wo er fehlt) und
`rework` (Arbeitsbaum dieses Repos, gemessen auf HEAD).

| Datei | Zweck |
| :- | :- |
| `fixture.py --case <name> --stand <s> --out <dir>` | baut das Test-Repo eines Falls für einen Stand |
| `cases/<name>/task.md`, `task-plugin.md`, `check.py` | Prompt ohne Plugin, Prompt mit `/docket:implement` als letzter Zeile, Prüfskript (`pass\|fail <klasse> <kriterium>`) |
| `checklib.py` | was die Prüfskripte lesen: Fixture, stream-json, Transkripte |
| `run.py --stand <s> [--case <name>]… [--runs 3] [--jobs 3] [--model claude-opus-5-5]` | startet fehlende, ungültige und (rework) veraltete Läufe; `--list [--stand <s>]` nennt die Fälle |
| `report.py --stand <s>… [--case]… [--min-pass n] [--not-below <s>]… [--table]` | liest nur `results/<stand>/<case>-<n>.json` |
| `results/BASELINE.md` | `report.py --table --stand none --stand 0.5.11` |

Ein Lauf: frische Fixture unter `%TEMP%/docket-evals/runs/<stand>/<case>-<n>-<zeit>/fx`, daneben `stream.jsonl`,
`stderr.txt` und `transcript/` (Kopie aus `~/.claude/projects`). Keine Grenze für Zeit, Züge oder Kosten.

Aufruf je Lauf, Ergebnis von 0.1 (a):

```
env ohne CLAUDE*, ANTHROPIC*, MCP_* plus CLAUDE_CODE_DISABLE_CLAUDE_MDS=1
claude -p <Prompt> --model <m> --effort high --permission-mode auto --output-format stream-json --verbose \
  --setting-sources project,local --strict-mcp-config [--plugin-dir <Stand>]
```

## 0.1 Probeläufe (2026-11-11, Claude Code 2.1.322)

### 0.1 (a)

Frage: Welcher Aufruf hält `~/.claude` und das installierte docket 0.5.11 aus dem Stand heraus?

- Ohne Zusatz: `system/init` nennt docket 0.5.11 aus dem Plugin-Cache, mattpocock-skills, process-cleanup,
  den Befehl `orchestrate-execution`, die Agenten `spec-fixer` und `spec-implementer`, Output-Style `Concise`,
  sechs claude.ai-Konnektoren.
- `--setting-sources project,local`: keine Nutzer-Plugins, Befehle, Agenten; Output-Style `default`;
  `permissionMode` `default`. `~/.claude/CLAUDE.md` und `KOMMUNIKATION.md` laden trotzdem (Antwort des Modells auf
  die Frage nach geladenen Memory-Dateien: beide Pfade).
- Dazu `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`: Antwort `NONE`. Dazu `--strict-mcp-config`: `mcp_servers` leer.
- Die Variablen der aufrufenden Sitzung (`CLAUDE_CODE_*`, `CLAUDECODE`, `ANTHROPIC_BASE_URL`, `MCP_*`) entfernt
  `run.py`; die Anmeldung kommt dann aus `~/.claude/.credentials.json` (Lauf mit leerem Umfeld: Antwort `ok`). Ein
  eigenes `CLAUDE_CONFIG_DIR` ohne Anmeldung endet mit `Not logged in`.
- Beleg je Stand (`system/init`, Fixture `two-points`, Haiku, ein Zug): `none` lädt kein Plugin außer den vier
  `@builtin`; `0.5.11` lädt `docket 0.5.11 docket@inline` aus dem Export; `rework` lädt `docket 0.5.11 docket@inline`
  aus dem Arbeitsbaum. Kein Stand nennt einen Befehl oder Agenten aus `~/.claude/commands` oder `~/.claude/agents`.
  `run.py` prüft das je Lauf (`plugin_problem`).

Ergebnis: Aufruf gefunden: Umfeld ohne `CLAUDE*`/`ANTHROPIC*`/`MCP_*` plus `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `--setting-sources project,local --strict-mcp-config`, für Plugin-Stände `--plugin-dir`; `system/init` nennt je Stand genau das erwartete docket oder keines und nichts aus `~/.claude`.

### 0.1 (b)

Frage: Expandiert `/docket:implement <Id>` als letzte Zeile eines `-p`-Prompts, und laufen die Hooks?

0.5.11-Fixture `two-points`, Prompt `cases/two-points/task-plugin.md`, Haiku, zwei Züge. `docket/LOG.md` danach:
`sealed` und `judge-suspended` für `s-b2000001`. Das Transkript enthält den Befehlstext (`# Implement mode (sealed)`)
und die Hook-Zeile `Mode sealed, spec s-b2000001`.

Ergebnis: ja; der Befehl expandiert, `UserPromptSubmit` versiegelt (LOG `sealed`), der Judge wird ausgesetzt.

### 0.1 (c)

Frage: Ist das PowerShell-Werkzeug im Headless-Lauf verfügbar?

`system/init` nennt `PowerShell` in `tools` (alle drei Stände). Fixture `precondition-powershell`, Stand `none`,
Haiku: Aufruf `PowerShell` mit `(Get-Content config/server.json | ConvertFrom-Json).cors.enabled`, Ergebnis `True`.

Ergebnis: ja; der Fall `precondition-powershell` braucht keinen Umweg über `powershell -NoProfile -Command`.

### 0.1 (d)

Frage: Steht der Befehlstext nach `/docket:implement` und einem erzwungenen `/compact` noch im Kontext?

0.5.11-Fixture `two-points`, Sonnet: Zug 1 mit `cases/two-points/task-plugin.md`, Zug 2 `/compact` (`--resume`),
Zug 3 die Frage nach der Überschrift des Abschnitts, der mit „Nothing of yours until the run ends“ beginnt
(`commands/implement.md:36`, `## What the user sees`). Antwort ohne Werkzeugaufruf: `NOT IN CONTEXT`. Im
Transkript steht nach der `compact_boundary` weder `# Implement mode (sealed)` noch `0. Resume or start`.

Ergebnis: nein; der Text eines Befehls unter `commands/` ist nach `/compact` weg, der Lauf-Text zieht in Schritt 2 nach `skills/implement/SKILL.md`.

### 0.1 (e)

Frage: Erreicht `additionalContext` eines PreToolUse-Hooks das Modell?

Probe-Plugin mit einem PreToolUse-Hook auf `Bash`, der
`{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "[probe] The code word is PAPAYA-42."}}`
ausgibt; Sonnet ruft `echo hello` und wird nach dem Codewort gefragt. Antwort: `PAPAYA-42`. Im Transkript steht es
als Anhang `hook_additional_context`.

Ergebnis: ja; `additionalContext` aus PreToolUse erreicht das Modell als Anhang `hook_additional_context`.

### 0.1 (f)

Frage: Wie verläuft ein Lauf `two-points` je Stand (Opus, Effort high)?

`python evals/run.py --stand <s> --case two-points --runs 1`, je Stand ein Lauf, gleichzeitig. Die Läufe zählen als
`two-points-1` der Messung (`results/<stand>/two-points-1.json`; der `rework`-Lauf ist ab dem ersten Commit von
Schritt 1 veraltet).

| Stand | Kriterien | Züge (Hauptsitzung) | Dauer | Kosten (Listenpreis) |
| :- | :- | :- | :- | :- |
| `none` | result 4/4 | 7 | 0,6 min | 0,17 USD |
| `0.5.11` | result 4/4, docket 2/2 | 78 | 7,3 min | 2,39 USD |
| `rework` (= 0.5.11) | result 4/4, docket 2/2 | 85 | 7,7 min | 2,54 USD |

Befund am Messaufbau: Ein Lauf, dessen Hintergrund-Agenten weitere Züge starten, schreibt je Zug ein
`result`-Ereignis (0.5.11: 13 Stück). `run.py` summiert `num_turns` über alle und misst `duration_ms` als Laufzeit des
Prozesses; die Unteragenten stehen im Stream mit `parent_tool_use_id` und zählen bei `tool_uses` mit.

Ergebnis: alle drei Stände bestehen jedes Kriterium; `none` 7 Züge in 0,6 min, `0.5.11` 78 Züge in 7,3 min, `rework` 85 Züge in 7,7 min.
