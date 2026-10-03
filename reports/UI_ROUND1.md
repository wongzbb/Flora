# UI Round 1 — Flora composer and execution details

## Scope

The terminal keeps Flora's existing Rich panels, pixel-art banner, colours and
anime-styled presentation. The conversation composer now uses a bordered
multiline prompt with a persistent bottom toolbar showing the current phase,
last result status, model, workspace, and active/total child count. The toolbar
also exposes the send/newline shortcuts without replacing the existing command
surface.

Verbose model-program and tool-result panels are collapsed by default so a long
run does not push the user-facing answer out of view. `/details` toggles those
panels for live inspection; `/log` remains the complete retained transcript and
audit path. Result values are grouped in a coloured `FLORA / RESULT` card while
the underlying reports and receipts remain unchanged.

## Verification

- `ruff check src/flora/terminal/ui.py src/flora/terminal/cli.py`: passed.
- `python3 -m compileall -q src/flora/terminal`: passed.
- Isolated temporary environment with the declared `rich` and
  `prompt-toolkit` versions: composer-toolbar/result-card smoke test passed.
- `PYTHONPATH=src python -m flora --help`: passed in the same environment.

This is a presentation-only change. It does not change scheduling, contracts,
receipts, evidence checks, budgets, or completion decisions. A full interactive
TTY screenshot test remains to be run on a terminal with a real provider
connection; non-interactive `--plain` output remains unchanged.
