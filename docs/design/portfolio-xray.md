# Portfolio X-Ray

Tells a user what they actually own across the funds they hold: how much of their
money is duplicated across funds, what the fees really cost, and how concentrated
they are. Someone holding three funds believes they are diversified three ways;
often most of that money is the same few hundred companies, bought three times.

## What it reports

| Output | Meaning |
|---|---|
| `overlap_pct` | Share of the portfolio sitting in securities held by two or more funds |
| `duplicated` | The top duplicated names (at most 15), with which funds hold each and the combined exposure |
| `blended_expense_ratio` | Fee across the whole portfolio, in percent |
| `annual_fee`, `fee_on_overlap` | Money figures, **only when the user entered amounts** |
| `concentration_top10_pct`, `largest_position` | Weight of the ten largest true positions once funds are collapsed |
| `complete_holdings`, `notes` | Whether the numbers are complete, and plain-language caveats |

## The maths

All of it is in `app/portfolio.py::build_xray`: pure, no network, exactly testable.
I/O (holdings, expense ratios) is gathered by `funds._xray_inputs` and passed in.

```
fund_weight_i    = amount_i / sum(amounts)          if EVERY fund has amount > 0
                 = 1 / n                            otherwise (equal weight)

holding_weight   = holding's weight / that fund's own total     (normalised per fund)
effective_weight = sum over funds of fund_weight_i x holding_weight
overlap_pct      = sum of effective_weight for securities held by 2+ funds
blended_er       = sum(fund_weight_i x er_i) / sum(fund_weight_i of funds that HAVE an er)
annual_fee       = total_amount x blended_er / 100                (amounts only)
fee_on_overlap   = annual_fee x overlap_pct / 100                 (amounts only)
```

Known-answer cases the tests lock in: identical funds are 100% overlapped,
disjoint funds 0%, a fund sharing exactly half its weight 50%.

## Rules that must not change

1. **Money figures only when the user supplied amounts.** Without amounts the funds
   are equal-weighted, that assumption is stated in `notes`, and no currency figure
   is emitted at all. An invented portfolio size would be exactly the class of
   fabricated number this project has already had to remove once.
2. **Amount-weighting is all-or-nothing.** If only some funds have amounts, fall
   back to equal weighting. Weighting by partial amounts would treat the unpriced
   funds as worthless, which is worse than not weighting.
3. **Normalise each fund before combining.** N-PORT `pctVal` does not reliably
   total 100 (rounding, cash, excluded asset classes). Without normalising, a
   partially disclosed fund quietly counts for less than its real share.
4. **Partial holdings understate overlap, and must say so.** When a fund resolves
   only to its top-10 (`yfinance_top10`, no N-PORT), `complete_holdings` is false
   and a note says the real overlap is higher.
5. **Never drop a fund silently.** A fund whose holdings cannot be read is named in
   the notes as left out. An overlap figure computed over 3 of a user's 5 funds,
   presented as covering all 5, is an unstated assumption.
6. **A missing expense ratio is not 0%.** The blend is renormalised over funds that
   have a ratio, and the missing ones are named.
7. **One identity function.** `funds.holding_key` (ticker, else normalised name) is
   shared by the two-fund compare and the N-way X-Ray so the two agree.

## Flow and caching

Background-and-poll, like the other slow paths
([`data-flow.md`](../architecture/data-flow.md) §5). The result is cached per user
for 6 hours and **invalidated on every portfolio change**: adding a fund, removing
one, or setting an amount (`add_fund`, `remove_fund`, `PATCH /{sym}/amount`). The
X-Ray describes the whole portfolio, so it is wrong the moment the portfolio
changes, not when the TTL expires.

## Edge cases

- **Same security listed twice** in one fund: weights are summed.
- **Zero or negative weights** are ignored.
- **No funds**: status `empty` with a message, not an error.
- **Holdings that all normalise away**: returns the notes rather than a number.
- **Amount** is validated `0 <= amount <= 1e12`; `null` clears it and returns the
  fund to equal weighting.

## Scope

US only in v1: SEC N-PORT supplies full holdings. India needs AMFI portfolio
disclosures first (BACKLOG roadmap).

## Verification status

The maths and UI are verified against fixtures and in a browser. **The live SEC
N-PORT path for X-Ray has only been verified in production**, not in the sandbox,
which cannot reach `sec.gov`.

## Code and tests

`app/portfolio.py`, `app/funds.py` (`/portfolio/xray`, `/{sym}/amount`),
`web/app.js` (`_loadXray`, `_xrayRender`); `tests/test_portfolio.py`.
