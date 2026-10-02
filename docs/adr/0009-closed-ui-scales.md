# 9. The UI uses closed type, space and radius scales

- **Status:** Accepted
- **Date:** 2026-08-31
- **Implemented in:** `eba02cd`; rules in `CLAUDE.md`; tokens in `web/styles.css`

## Context

`web/styles.css` had accumulated **19 font sizes, 21 spacing steps and 13 border
radii**, including half-pixel steps (10.5, 11.5, 12.5, 13.5 px). Nobody designs an
11.5 px step. It appears when each feature is nudged until it looks right on its own
screen and never reconciled with the rest, and that incoherence is what makes an
interface read as machine-assembled. The colour system was already good (semantic
tokens); the type, space and radius scales were simply bypassed.

## Decision

The scales are **closed sets**, defined as tokens at the top of `web/styles.css`:

| Axis | Values |
|---|---|
| Type | 11 / 12 / 13 / 15 / 18 / 22 / 28 px |
| Space | 2 / 4 / 6 / 8 / 12 / 16 / 24 / 32 px |
| Radius | 4 / 8 / 12 px, plus pill |

Use a token; do not introduce a raw value. Adding a step is a design decision made in
the token block with a reason. Values above 32 px are layout offsets, not rhythm (for
example `.main-col`'s 90 px bottom padding clears the floating Ask AI button) and are
left alone.

## Consequences

- 257 values were mapped onto the scales: 19 font sizes became 7, 21 spacing steps
  became 8, 13 radii became 4.
- **A scale decides the vocabulary, not which word fits.** The mechanical pass had two
  silent regex bugs (one anchored to line starts, one that consumed the `;` separator
  so every other declaration was skipped), and its rounding wrapped the sidebar:
  nav items sat at 13.5 px and rounded *up* to 15 px, which broke "Coverage & Leaders"
  across two lines. Both were caught only by looking at the rendered page, never by
  the substitution count. Check the browser, not just the number.
- A one-line audit in `CLAUDE.md` prints the values in use; it should list only the
  closed set plus the documented layout offsets.
- Still open: about 62 raw colours bypass the `:root` tokens, and the glass-card
  pattern is repeated inline rather than extracted as a `.panel` primitive.

## Alternatives considered

- **Leave as is:** the drift compounds with every feature.
- **A CSS framework or utility library:** a build step and a dependency for a
  three-file frontend.
