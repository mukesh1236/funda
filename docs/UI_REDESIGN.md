# Dashboard UI Redesign — Design Spec

*Implemented in `web/` (index.html, styles.css, app.js additions). This doc is
the design record: layout, palette, component hierarchy, and the rationale.*

## Layout & structure

```
app-shell (flex)
├── sidenav (218px, sticky, glass)
│   ├── brand (logo + AlphaFunds)
│   ├── nav-group "Markets"
│   │   ├── Overview          (view: feed)
│   │   ├── Coverage & Leaders(view: leaderboard)
│   │   ├── Watchlist         (view: watchlist)
│   │   ├── Funds             (view: funds)
│   │   └── Market Digest     (view: digest)
│   ├── nav-group "Operations"
│   │   ├── SRE Dashboard     (view: sre)        ← new
│   │   └── 🔒 Admin          (gold accent, admin-only)
│   └── sidenav-foot (live pulse dot + last-updated timestamp)
└── main-col
    ├── topbar (sticky)
    │   ├── global search (ticker/company, /api/search, → opens stock detail)
    │   └── actions: market · segment · window · Refresh(↻ spins) · auth
    ├── stats        (4 KPI glass tiles: tracked / sources / auto-refresh / updated)
    ├── highlights   (analyst calls · movers · coverage · strongest buy/sell)
    └── main → #status + #content (per-view render)

floating: Ask-AI assistant (gradient FAB → glass panel with suggestion chips)
overlays: auth, welcome (unchanged logic, retheme only)
```

All pre-existing element IDs and the `.tab[data-view]` mechanism were preserved,
so 1,100+ lines of working view logic in `app.js` needed no rewrite — the
redesign is a re-skin plus additive features.

## Color palette (validated)

Chart/status colors were run through the dataviz six-checks validator against
the actual card surface (`#141d2f`): lightness band, chroma floor, CVD
separation (worst adjacent ΔE 35.9 vs ≥12 target), and ≥3:1 contrast — all pass.

| Role | Hex | Use |
|---|---|---|
| Page plane | `#0b1220` | deep navy background + blue/green radial gradients |
| Card glass | `rgba(255,255,255,.04)` + `backdrop-filter: blur(10px)` | all panels |
| Accent | `#3987e5` | interactive elements, active nav, links, series-1 |
| Success | `#0ca30c` / `#2ecc71` | buy counts, SLO ok, live dot |
| Warning | `#fab219` | medium confidence, SEV2, demo-data note |
| Error | `#d03b3b` / `#e66767` | sell counts, SEV1, misses |
| Admin | `#d4a017` gold | admin nav + admin panels (visually distinct + 🔒) |
| Ink | `#e8edf6` / `#aab6c8` / `#7c899d` | primary / secondary / muted |

Typography: **Inter** (Google Fonts, system-ui fallback), tabular numerals on
all numeric columns.

## Data visualization

- **Consensus strength meter** under each stock's B/H/S counts — proportional
  buy-vs-sell fill with a 2px surface gap (dataviz spacer rule).
- **Ticker monograms** — deterministic hue per symbol (8 fixed hues, hashed),
  no external logo dependency.
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
`docs/TEAMOPS_DESIGN.md`) isn't deployed anywhere funda can reach yet; the
markup consumes the same shape as TeamOps' `GET /api/dashboard/sre`, so wiring
it live is a fetch-swap.

## Interaction & UX

- Hover: cards lift 2px + border brighten; rows tint accent; nav slides 2px.
- Refresh button spins (`.working`) during the 45s background refresh window.
- Global search debounced 250ms, Enter selects first hit, Esc dismisses.
- Ask-AI: gradient pill FAB, rising panel animation, contextual suggestion
  chips that submit on click; scope label shows market · view/symbol.
- Admin: gold accent + lock icon; only rendered for role=admin (unchanged RBAC).
- Responsive: <900px collapses the sidenav into a horizontal scroll bar.

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
