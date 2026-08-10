"""Portfolio X-Ray — what you actually own across the funds you hold.

Three questions a fund investor can't answer from a broker statement:

  1. **Overlap.** Someone holding VOO + VTI + FXAIX believes they're
     diversified across three funds. In reality most of that money is the same
     few hundred companies, bought three times.
  2. **What the fees really cost.** Not the expense ratio in isolation — the
     blended cost across the whole portfolio, and how much of it is being paid
     on exposure that's duplicated anyway.
  3. **Concentration.** The true weight of the largest positions once the funds
     are collapsed into the securities underneath them.

The math here is pure and network-free so it can be tested exactly; all I/O
(holdings, expense ratios) is done by the caller and passed in.

Two honesty rules shape the output, matching how the rest of this codebase
treats data quality:

- **Money figures only when the user told us amounts.** Without them the funds
  are equal-weighted, the assumption is stated in `notes`, and no currency
  figure is emitted at all. An invented portfolio value would be exactly the
  class of fabricated number this project has already had to remove once.
- **Partial holdings understate overlap.** When a fund only resolves to its
  top-10 (no SEC N-PORT filing), the true overlap is higher than reported —
  said plainly rather than presented as a precise number.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Report at most this many duplicated names — beyond it the list stops being
# readable and starts being a data dump.
_MAX_DUPLICATED = 15
_CONCENTRATION_TOP_N = 10


@dataclass
class FundInput:
    """One fund in the user's portfolio, with everything needed to place it.

    `holdings` are rows of {ticker?, name, weight} as returned by
    app/funds.py::_load_all_holdings. `source` is 'nport' (complete) or
    'yfinance_top10' (partial — overlap will be understated).
    """
    symbol: str
    holdings: List[dict]
    expense_ratio: Optional[float] = None    # already a percent, e.g. 0.04
    amount: Optional[float] = None           # user's holding value; None = unknown
    source: str = "nport"
    name: Optional[str] = None


@dataclass
class XRayResult:
    overlap_pct: float = 0.0
    duplicated: List[dict] = field(default_factory=list)
    concentration_top10_pct: float = 0.0
    largest_position: Optional[dict] = None
    blended_expense_ratio: Optional[float] = None
    total_amount: Optional[float] = None
    annual_fee: Optional[float] = None
    fee_on_overlap: Optional[float] = None
    amount_weighted: bool = False
    complete_holdings: bool = True
    fund_count: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overlap_pct": self.overlap_pct,
            "duplicated": self.duplicated,
            "concentration_top10_pct": self.concentration_top10_pct,
            "largest_position": self.largest_position,
            "blended_expense_ratio": self.blended_expense_ratio,
            "total_amount": self.total_amount,
            "annual_fee": self.annual_fee,
            "fee_on_overlap": self.fee_on_overlap,
            "amount_weighted": self.amount_weighted,
            "complete_holdings": self.complete_holdings,
            "fund_count": self.fund_count,
            "notes": self.notes,
        }


def _fund_weights(funds: List[FundInput]) -> tuple:
    """(weights, amount_weighted). Weights sum to 1.

    Amount-weighted only when EVERY fund has a positive amount — a portfolio
    where two of five funds have values would otherwise silently treat the
    other three as worthless, which is worse than not weighting at all.
    """
    amounts = [f.amount for f in funds]
    if all(a is not None and a > 0 for a in amounts):
        total = sum(amounts)
        return [a / total for a in amounts], True
    n = len(funds)
    return [1.0 / n] * n, False


def _normalised_holdings(holdings: List[dict]) -> Dict[str, dict]:
    """{key: {name, ticker, weight_fraction}} for one fund, weights summing to 1.

    N-PORT `pctVal` doesn't reliably total 100 (rounding, excluded asset
    classes, cash), so each fund is normalised against its own total before
    being combined with others. Skipping this would let a fund with a partial
    disclosure quietly count for less than its real share of the portfolio.
    """
    from app.funds import holding_key

    rows: Dict[str, dict] = {}
    for h in holdings:
        key = holding_key(h)
        if not key:
            continue
        weight = float(h.get("weight") or 0)
        if weight <= 0:
            continue
        if key in rows:                      # same security listed twice
            rows[key]["weight"] += weight
            continue
        rows[key] = {"key": key, "name": h.get("name") or h.get("ticker") or key,
                     "ticker": h.get("ticker"), "weight": weight}

    total = sum(r["weight"] for r in rows.values())
    if total <= 0:
        return {}
    for r in rows.values():
        r["weight"] = r["weight"] / total
    return rows


def build_xray(funds: List[FundInput],
               excluded: Optional[List[str]] = None) -> XRayResult:
    """Collapse N funds into the securities underneath them.

    `excluded` names funds the caller couldn't load holdings for. They are
    reported rather than ignored: an overlap figure covering 3 of a user's 5
    funds, presented as if it covered all 5, is the same kind of unstated
    assumption the money rule above exists to prevent.
    """
    funds = [f for f in funds if f.holdings]
    result = XRayResult(fund_count=len(funds))
    excluded = list(excluded or [])

    def note_excluded(analysed: bool) -> None:
        """Called on every exit path — a fund left out of the maths is reported
        whether or not the remaining funds produced an analysis."""
        if not excluded:
            return
        result.complete_holdings = False
        names = ", ".join(excluded)
        verb = "is" if len(excluded) == 1 else "are"
        result.notes.append(
            f"{names} could not be read and {verb} left out of everything above."
            if analysed else f"No holdings are published yet for {names}.")

    if not funds:
        result.notes.append("Add at least one fund to see what you actually own.")
        note_excluded(analysed=False)
        return result

    weights, amount_weighted = _fund_weights(funds)
    result.amount_weighted = amount_weighted

    # security key -> {name, ticker, effective weight, which funds hold it}
    combined: Dict[str, dict] = {}
    for fund, fw in zip(funds, weights):
        for key, row in _normalised_holdings(fund.holdings).items():
            slot = combined.setdefault(key, {
                "name": row["name"], "ticker": row["ticker"],
                "effective_weight_pct": 0.0, "held_by": [],
            })
            slot["effective_weight_pct"] += fw * row["weight"] * 100
            slot["held_by"].append(fund.symbol)

    if not combined:
        result.notes.append("No holdings data is available for these funds yet.")
        note_excluded(analysed=False)
        return result

    duplicated = [c for c in combined.values() if len(c["held_by"]) > 1]
    duplicated.sort(key=lambda c: c["effective_weight_pct"], reverse=True)
    result.overlap_pct = round(sum(c["effective_weight_pct"] for c in duplicated), 2)
    result.duplicated = [
        {**c, "effective_weight_pct": round(c["effective_weight_pct"], 3)}
        for c in duplicated[:_MAX_DUPLICATED]
    ]

    ranked = sorted(combined.values(),
                    key=lambda c: c["effective_weight_pct"], reverse=True)
    result.concentration_top10_pct = round(
        sum(c["effective_weight_pct"] for c in ranked[:_CONCENTRATION_TOP_N]), 2)
    top = ranked[0]
    result.largest_position = {
        "name": top["name"], "ticker": top["ticker"],
        "effective_weight_pct": round(top["effective_weight_pct"], 3),
        "held_by": top["held_by"],
    }

    # ── fees ──────────────────────────────────────────────────────────────────
    rated = [(f, w) for f, w in zip(funds, weights) if f.expense_ratio is not None]
    if rated:
        covered = sum(w for _, w in rated)
        if covered > 0:
            # Renormalise over the funds we actually have a ratio for, so a
            # missing expense ratio doesn't read as a 0% fee.
            result.blended_expense_ratio = round(
                sum(f.expense_ratio * w for f, w in rated) / covered, 4)
        if len(rated) < len(funds):
            missing = ", ".join(f.symbol for f in funds if f.expense_ratio is None)
            result.notes.append(
                f"No expense ratio available for {missing} — the blended fee "
                f"below covers the other funds only.")

    if amount_weighted and result.blended_expense_ratio is not None:
        total_amount = sum(f.amount for f in funds)
        result.total_amount = round(total_amount, 2)
        result.annual_fee = round(total_amount * result.blended_expense_ratio / 100, 2)
        result.fee_on_overlap = round(result.annual_fee * result.overlap_pct / 100, 2)
    elif not amount_weighted:
        result.notes.append(
            "Your funds are treated as equal-sized because no amounts were "
            "entered. Add what you hold in each to see the real cost in money.")

    # ── coverage honesty ──────────────────────────────────────────────────────
    partial = [f.symbol for f in funds if f.source != "nport"]
    if partial:
        result.complete_holdings = False
        result.notes.append(
            f"Only the largest holdings are published for {', '.join(partial)}, "
            f"so the real overlap is higher than shown.")

    note_excluded(analysed=True)
    return result
