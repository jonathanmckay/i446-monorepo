# dream-tui

Minimal terminal UI for grading a Dream morning brief in one sitting.

## Why

Typing "1 go, 2 ack, 3 go, …" in chat is slow. This lets JM grade 20 cards in ~60 seconds with single keypresses.

## Usage

```bash
python3 ~/i446-monorepo/tools/dream-tui/dream.py
```

(Picks the latest dream-run dir by default. Pass a path to override.)

Per card: press the choice letter (`a`/`b`/`c`/`d`), then a grade (1–5). Space to skip, `q` to quit.

Grades written to `<run-dir>/grades.json`. Dream reads this on the next run to learn JM patterns and execute chosen options.

## Requires

Dream v7+ — earlier runs don't emit `cards.json`.
