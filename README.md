# Flora overview

**A programmable core for agents.**

*Act to get things done—and to find things out.*

The English project overview introduces the terminal-first General Agent, execution core, source code, documentation, and validation. The paper is marked as in preparation.

The General Agent supports web research, document and spreadsheet processing, external tools, resumable conversations, and read-only subagent delegation. It uses Flora's existing execution kernel rather than replacing it with per-tool replanning.

The overview presents the two principles behind Flora: diagnostic actions that inform subsequent decisions, and local checks for program revisions derived from observed tool results and their use in subsequent steps. These mechanisms are introduced before their technical names: dual control and synthesizable contracts.

## Site contents

- Self-contained HTML with inline styles, scripts, pixel art, and logo. No external fonts, images, or analytics.
- `flora-logo.svg` is the original pixel flower logo with a transparent background.
- The application card, source links, onboarding instructions, and footer point to the General Agent on `general-agent`.
- The [General Agent README](https://github.com/wongzbb/Flora/blob/general-agent/README.md) provides installation and terminal usage instructions, optional performance settings, known limitations, and regression-test instructions. Validation links lead to the application's current test instructions and documented limitations.
- Two pseudocode views illustrate the execution loop and program model. Ellipses omit implementation details; the examples are conceptual rather than executable APIs. Each view links to the corresponding implementation on the `core` branch.
- Project links point to `general-agent`, `core`, and `docs`. GitHub repository permissions apply.
- Responsive layouts and keyboard navigation are included. Native radio controls and CSS switch the code views without JavaScript; each view includes its own implementation link. The copy button is enabled when JavaScript runs.
- Model errors, response-time outliers, and network restrictions remain possible. Performance settings retain the execution kernel; a normal completion status does not establish task success.

## Publishing and local preview

GitHub Pages publishes the root of the `overview` branch at [wongzbb.github.io/Flora](https://wongzbb.github.io/Flora/). This branch contains only the static site; the General Agent implementation remains on `general-agent`, and the full manual remains on `docs`.

Download `index.html` and open it in a browser for an offline preview. The page itself has no external asset dependencies; following project links requires network access.
