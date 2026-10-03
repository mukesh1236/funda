# Dashboard UI Redesign — Design Spec

*Implemented in `web/` (index.html, styles.css, app.js additions). This doc is
the design record: layout, palette, component hierarchy, and the rationale.*

> **Current state.** The shell and Overview were redesigned in the "Clarity" identity
> ([ADR 0011](../adr/0011-visual-identity-and-tokens.md)): top navigation, light and dark
> themes, self-hosted Inter, an SVG icon sprite. The scales are closed
> ([ADR 0009](../adr/0009-closed-ui-scales.md)); the rules and audit command are in
> [`CLAUDE.md`](../../CLAUDE.md). Sections below that describe the old sidebar, the navy
> glass palette or Google Fonts are history; this section and the next two are current.

## Layout & structure

```
app-shell
└── main-col (max 1320px, centred)
    ├── topbar (sticky)
    │   ├── row 1: brand · primary nav (.tab[data-view]) · Ask AI · theme toggle · account menu
    │   │          (ops nav: SRE + Admin, role=admin only)
    │   └── row 2: search · market seg · window seg · segment select · refresh · "Updated"
    ├── stats        (4 market KPIs derived from the feed payload)
    ├── highlights   (12-col grid: analyst calls · movers · coverage · strongest buy/sell)
    └── main → #status + #content (per-view render)

mobile (<=900px): nav moves to a bottom tab bar (same .tab[data-view] buttons); the account
menu gains Refresh and the admin views; the chat opens as a sheet over the tab bar.
overlays: auth, welcome, WhatsApp, chat panel (token-themed)
```

The market and window controls are segmented buttons over hidden `<select id="market">`
and `<select id="days">`; the selects stay the source of truth, so every loader is
unchanged. All `.tab[data-view]` and element IDs the logic relied on were preserved.

**Rules that must not change**

- KPIs are derived only from the feed payload; never add a metric the feed cannot support.
- Gain/loss always carries an arrow or sign, not colour alone.
- The theme is decided in CSS from `prefers-color-scheme` and `[data-theme]`; JS only
  records the user's explicit choice. A stored choice is applied by the inline script in
  `<head>` before the stylesheet loads.
- `#themeToggle`, not `#theme` (that id is the segment select).
- A background auto-refresh keeps the table on screen; only a user-driven load shows the
  skeleton.
- An opened stock row leads with the answer: "Why analysts recommend it" (headline plus
  three reasons) and six headline fundamentals. Everything else (more reasons, all
  fundamentals, analyst calls, big investors, news) sits in collapsed `<details>` expanders
  built by `moreSection()`. Two Yahoo Finance links (latest news, full quote) are always
  shown, so a stock whose news feed came back empty still has somewhere to go next.
- The opened row draws in two stages: the instant part (why, analyst calls) first, then
  fundamentals, big investors and news into placeholders. See
  [`analyst-summary.md`](analyst-summary.md).
- News links in the highlight cards use the accent colour and keep their text label on
  mobile; an icon alone was easy to miss.

## Colour

Tokens are in `web/styles.css`: semantic names (`--bg`, `--surface`, `--surface-2`,
`--text`, `--muted`, `--accent`, `--accent-fill`, `--gain`, `--loss`, `--warn` and their
`-soft` fills), defined for light on `:root` and for dark in two identical blocks. Legacy
names (`--bg-glass`, `--good-hi`, ...) alias the new ones. `tests/test_design_tokens.py`
asserts contrast for both themes, so the palette values are not repeated here.

Typography: **Inter**, self-hosted from `web/fonts/` (latin + latin-ext for the rupee
sign), tabular numerals wherever digits line up in columns.

## Data visualization

- **Consensus bar** per stock: proportional buy / hold / sell segments with the score and
  B/H/S counts beneath (the counts keep it readable without colour).
- **Ticker monograms** — a neutral tile with the first three letters; no per-ticker hue
  (it would need a second palette for dark mode) and no external logo dependency.
- **Watchlist sparklines** (existing) recolored to series-1 via CSS var.
- **SRE view**: single-series SVG line charts (latency, error rate — one axis
  each, never dual-axis), single-hue sequential heatmap for errors-by-hour,
  SLO progress bars, reserved status colors for severity badges (icon + label,
  never color alone).
- Tooltips via `title` on meters, heat cells, confidence badges (the existing
  “why?” links keep their contextual hints).

## SRE Dashboard tab

Renders uptime, p95 latency trend, error-rate trend, errors-by-hour heatmap,
SLO compliance bars, and an incidents table with SEV badges. Currently fed by
**demo data** (clearly labeled) because the TeamOps backend (see
`docs/archive/TEAMOPS_DESIGN.md`) isn't deployed anywhere funda can reach yet; the
markup consumes the same shape as TeamOps' `GET /api/dashboard/sre`, so wiring
it live is a fetch-swap.

## Interaction & UX

- Hover: rows tint; nav items tint. Rows open from the keyboard (Enter/Space).
- Refresh icon spins (`.working`) during the 45s background refresh window.
- Global search debounced 250ms, Enter selects first hit, Esc dismisses.
- Ask-AI: header button, rising panel animation, contextual suggestion
  chips that submit on click; scope label shows market · view/symbol.
- Admin: gold accent + lock icon; only rendered for role=admin (unchanged RBAC).
- Responsive: <=900px swaps the top nav for a bottom tab bar (see Layout).

## Library recommendations (when charts outgrow hand-rolled SVG)

| Need | Recommendation | Why |
|---|---|---|
| Sparklines/line/bar at this scale | **Keep inline SVG** (current) | zero deps, full theme control, <100 LOC |
| Rich interactivity (crosshair, zoom, brush) | **ECharts** | best dark-theme + finance defaults, canvas perf on big series |
| React migration path | **Recharts** | declarative, composes with a future Next.js rewrite (BACKLOG I5) |
| Bespoke visual identity later | **D3** | only when a designer-led custom viz becomes a differentiator |

Chart.js was considered and skipped: ECharts covers the same ground with
better financial-dashboard ergonomics and theming.

## Verified

Rendered live and screenshot-tested with Playwright/Chromium at 1440×900 and
390×844 (overview, SRE, leaderboard, funds, chat open, mobile). Caught and
fixed during verification: author `display` rules resurrecting `[hidden]`
overlays (now globally guarded), and an `&amp;` literal in the funds status line.
