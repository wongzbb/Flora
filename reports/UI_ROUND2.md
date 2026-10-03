# UI Round 2 — bottom composer anchoring

The first UI round pinned only the prompt-toolkit toolbar. In a long result
screen the editable `flora ›` line was still left immediately after the result,
with empty space before the toolbar. This round fixes the actual position:

* the Rich live execution view uses a vertical layout with a reserved composer
  panel at the bottom;
* before each idle prompt, the terminal cursor moves to the row immediately
  above the toolbar and the old composer strip is cleared;
* the Rich result cards, pixel-art banner, colors, `/details`, and `/log` are
  unchanged.

Verification: the current source rendered a fixed `COMPOSER` layout in the Rich
smoke test, and a pseudo-terminal capture contained the bottom-row cursor
anchor escape before the prompt. A full-screen human screenshot should still be
checked after terminal resizing because cursor positioning is terminal-specific.

