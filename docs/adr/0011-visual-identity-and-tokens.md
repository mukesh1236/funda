# 11. Visual identity "Clarity": light and dark themes on semantic tokens

- **Status:** Accepted
- **Date:** 2026-10-02
- **Implemented in:** this change (shell + Overview); the other views inherit the tokens and are not yet redesigned

## Context

The interface read as machine-assembled rather than designed. The biggest tells were
emoji and text glyphs used as icons (the logo, nav icons, flags), a header crowded with
three selects and four buttons, KPI tiles that described the *system* ("3 data sources",
"never last updated") instead of the market, one dark theme only, and about 62 raw colours
that bypassed the tokens (ADR 0009 left that open). Inter was loaded from Google Fonts, so
screenshots taken without network access used a different, wider fallback font.

No tool removes the design decision. Three directions (Ledger, Terminal, Clarity) were
mocked from the same seeded feed and the owner chose **Clarity**, with a dark theme and a
light/dark toggle on desktop and mobile.

## Decision

- **Identity:** cool slate neutrals, indigo accent, spacious cards with a soft shadow,
  top navigation instead of a sidebar. Inter, self-hosted.
- **Two themes, one token set.** Semantic colour tokens (`--bg`, `--surface`, `--text`,
  `--accent`, `--gain`, `--loss`, ...) are defined on `:root` (light); the same set is
  redefined for dark in an `@media (prefers-color-scheme: dark)` block and again under
  `[data-theme="dark"]`, so the OS setting and the toggle give the same result. The toggle
  stores `localStorage['theme']`; with nothing stored, the OS decides. An inline script in
  `<head>` applies a stored choice before first paint.
- **Legacy tokens stay as aliases** (`--bg-glass`, `--good-hi`, ...) pointing at the new
  names, so views that were not redesigned follow the theme without edits.
- **Gain and loss are never colour alone:** an arrow, a sign or a word always accompanies
  them.
- **Icons:** an inline SVG sprite (Lucide, ISC) in `index.html`; no icon font, no request.
- **Overview KPIs describe the market** and are derived only from the feed payload already
  fetched (net-buy names, buy-rating share, strongest consensus, targets hit). System
  facts (stocks tracked, sources, scheduler) stay in the admin views.
- **Header:** brand, primary nav and tools on one row; search, segmented market/window
  controls, segment select and refresh on the next. Facts, WhatsApp, sign in/out and (on
  mobile) refresh and the admin views live in an account menu. Mobile gets a bottom tab bar.
  The segmented controls front hidden `<select>`s, which remain the single source of
  truth that `app.js` reads.
- **Scales unchanged** (type 11-28, space 2-32, radius 4/8/12): the closed sets of ADR 0009
  survive the redesign intact. What changes from ADR 0009: colour is now fully tokenised
  (enforced by `tests/test_design_tokens.py`), panels are opaque surface cards with
  `--shadow` rather than blurred glass, and the 90px bottom padding now clears the mobile
  tab bar rather than a floating button.
- **Contrast is a test.** `tests/test_design_tokens.py` asserts WCAG AA for every
  text-on-surface pair in both themes (4.5:1 text, 3:1 graphics), light/dark parity, no raw
  colours outside the token blocks, and that fonts and icons referenced actually exist.

## Consequences

- Every new colour must be added to both themes and pass the contrast test; this is
  deliberate friction.
- Inter ships from `web/fonts/` (latin and latin-ext, ~130 KB), so there is no third-party
  request on load and screenshots are faithful. The OFL licence and the Lucide ISC licence
  ship in the repo (`web/fonts/OFL.txt`, `web/THIRD_PARTY_NOTICES.txt`).
- The Funds page, fact sheet, X-Ray, Watchlist, Leaderboard, Digest, Admin and SRE inherit
  the tokens but keep their old layouts, and some still contain emoji. Expect a mixed look
  until the second pass.
- Soft status fills are opaque tokens rather than translucent overlays, so contrast can be
  computed exactly; the cost is one extra token per status colour per theme.
- Visual taste is not testable. The contrast test proves legibility, not beauty.

## Alternatives considered

- **Ledger (warm, serif numerals) and Terminal (dense, monospace):** mocked and rejected by
  the owner; Terminal's density and Ledger's second typeface would each have needed a
  scale exception.
- **Adopting a CSS framework or component library:** a build step and a dependency for a
  three-file frontend (same reasoning as ADR 0009).
- **Generating screens with Figma or Stitch:** neither produces a design from a prompt in
  this environment; Figma reads finished designs only.
