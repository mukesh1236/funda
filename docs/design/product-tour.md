# Product tour

*Code: `web/app.js` (`TOUR_STEPS`, `startTour`, `_tourPlace`, `endTour`), `web/styles.css`
(`.tour-*`), `web/index.html` (`#tourBtn`, `#tourMenu`, `#welcomeTour`).
Tests: `tests/test_tour.py`.*

## What it is

A guided walkthrough of the app: 13 short steps, each pointing at a real element with a
dimmed spotlight and saying what the feature does and why it helps. It covers the market
snapshot tiles (including what "consensus" means), the highlights, a stock row, search and
filters, Leaders, Watchlist, Funds and fact sheets, Market Digest, Ask AI, the theme toggle,
the account menu, and where to replay the tour. Plain DOM and CSS: no library, no build step.

## When it appears

| Who | How |
|---|---|
| A brand-new account | Starts by itself right after sign-up (`_autoTourForNewAccount`). |
| Someone logging in to an existing account | Never pushed at them. |
| A visitor who is not signed in | The one-time welcome popup has a **Take the 1-minute tour** button. |
| Anyone, any time | **Compass button** in the header (desktop) or **Take a tour** in the account menu (phone). |

"Seen" is remembered **per browser and per account** in `localStorage['tour_done_<id>']`
(`tour_done_guest` when signed out). Finishing or skipping both count. Nothing is stored on
the server, so a new device shows it again to a new account; this was a deliberate choice
over adding a database column. Every storage access is wrapped in try/catch, as elsewhere.

## Rules that must not change

- **Steps point at elements by selector, and a missing target shows a centred card instead
  of failing.** The desktop nav is hidden on a phone, so every nav step lists both a
  `.topnav` and a `.tabbar` selector; the first *visible* one wins. `tests/test_tour.py`
  fails if a selector stops existing or a nav step loses its phone fallback.
- **The tour starts on the Overview** (it forces the view and waits for the table) because
  the first steps describe it.
- **The card never covers its target and always stays on screen.** A target taller than the
  screen (the stacked highlights on a phone) is clipped to what is visible. Targets are
  scrolled clear of the sticky header on desktop.
- **Accessible:** `role="dialog"`, `aria-modal`, labelled by the step title, focus kept
  inside the card, Escape closes, left/right arrows move, focus returns to the opener.
- **Theme-aware:** colours are tokens only; the dim is `--scrim`.
- **Copy must stay true.** A step that names a feature that is broken advertises it. The
  Funds step mentions fact sheets, which depend on `EDGAR_USER_AGENT` being set in
  production (see [`fund-factsheets.md`](fund-factsheets.md)).

## Adding or changing a step

Edit `TOUR_STEPS` in `web/app.js`: `sel` (candidate selectors), `title`, `body` (what it is,
then "Why it helps"). Keep it short. If the step names a new element, give it a stable id.
Run `pytest tests/test_tour.py`.
