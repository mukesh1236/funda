"""Pure aggregation + validation functions. No I/O, no mutation of inputs.

Two responsibilities:
  - compute_consensus: turn many analyst calls on one stock into a single
    rating by counting buys/holds/sells (the user's "add the counts" ask).
  - evaluate_outcome: decide whether a recommendation's target was hit, missed,
    still pending, or expired, given the current price.
"""
from datetime import date
from typing import List, Optional

from app.models import (
    AnalystRecommendation,
    Confidence,
    ConsensusOut,
    RecommendationOutcome,
)


# Sources that report an aggregate analyst headcount per stock (buy/hold/sell
# totals). Only these drive the consensus numbers. Named-detail sources
# (yahoo_upgrades, fmp, morningstar) list individual firms — they're shown on
# expand but NOT summed into the counts, since their analysts are already
# represented in the aggregate totals (counting both would double-count).
COUNTING_SOURCES = {"yahoo", "finnhub", "tipranks"}


def _latest_per_source(recs: List[AnalystRecommendation]) -> List[AnalystRecommendation]:
    """Keep only each source's most recent snapshot.

    Aggregate sources (Yahoo/Finnhub/TipRanks) re-emit their buy/hold/sell
    counts every day, so summing across days would inflate the totals. We treat
    a source's newest entry_date as its current view and drop older snapshots.
    """
    latest_date: dict[str, str] = {}
    for r in recs:
        d = r.entry_date or ""
        if d > latest_date.get(r.source, ""):
            latest_date[r.source] = d
    return [r for r in recs if (r.entry_date or "") == latest_date.get(r.source, "")]


def compute_consensus(recs: List[AnalystRecommendation]) -> Optional[ConsensusOut]:
    """Aggregate recommendations for ONE symbol into a consensus rating.

    Uses each source's latest snapshot only (see _latest_per_source) so repeated
    daily collection doesn't inflate counts. consensus_score = buy_count -
    sell_count. Returns None for an empty list; the first rec's symbol is used.
    """
    if not recs:
        return None

    symbol = recs[0].symbol
    recs = _latest_per_source(recs)

    # Counts come only from aggregate sources (see COUNTING_SOURCES); summing the
    # per-row counts lets a Yahoo bucket of 25 buys carry its full weight.
    counting = [r for r in recs if r.source in COUNTING_SOURCES]
    buy = sum(r.count for r in counting if r.action == "buy")
    hold = sum(r.count for r in counting if r.action == "hold")
    sell = sum(r.count for r in counting if r.action == "sell")

    targets = [r.target_price for r in recs if r.target_price and r.target_price > 0]
    avg_target = round(sum(targets) / len(targets), 2) if targets else None

    # Sources/firms reflect everything we have (named detail included).
    sources = sorted({r.source for r in recs if r.source})
    firms = sorted({r.firm for r in recs if r.firm})
    latest = max((r.entry_date for r in recs if r.entry_date), default=None)

    return ConsensusOut(
        symbol=symbol,
        buy_count=buy,
        hold_count=hold,
        sell_count=sell,
        total_count=buy + hold + sell,
        consensus_score=buy - sell,
        avg_target=avg_target,
        latest_entry_date=latest,
        sources=sources,
        firms=firms,
    )


def _days_between(start: str, end: date) -> int:
    """Whole days from an ISO date string to `end`. 0 if start unparseable."""
    try:
        start_d = date.fromisoformat(start)
    except (ValueError, TypeError):
        return 0
    return max(0, (end - start_d).days)


def evaluate_outcome(
    rec: AnalystRecommendation,
    current_price: float,
    horizon_days: int = 365,
    today: Optional[date] = None,
) -> RecommendationOutcome:
    """Validate one recommendation's target against the current price.

    Direction:
      - buy:  hit when current_price >= target_price
      - sell: hit when current_price <= target_price
      - hold: not directionally testable → pending, then expired past horizon
    A rec with no target_price can't be evaluated → pending / expired.
    `missed` is only assigned to directional (buy/sell) calls whose horizon
    elapsed without hitting the target.
    """
    today = today or date.today()
    days_held = _days_between(rec.entry_date, today)
    last_checked = today.isoformat()
    target = rec.target_price
    elapsed = days_held >= horizon_days

    pct_to_target: Optional[float] = None
    if target and target > 0 and current_price > 0:
        pct_to_target = round((current_price - target) / target * 100, 2)

    # No usable target, or a non-directional hold → can't judge hit/miss.
    if not target or target <= 0 or rec.action == "hold":
        status = "expired" if elapsed else "pending"
        return RecommendationOutcome(
            rec_id=rec.rec_id, symbol=rec.symbol, current_price=current_price,
            target_price=target, pct_to_target=pct_to_target,
            status=status, days_held=days_held, last_checked=last_checked,
        )

    if rec.action == "buy":
        hit = current_price >= target
    elif rec.action == "sell":
        hit = current_price <= target
    else:  # defensive — unknown action treated as non-directional
        hit = False

    if hit:
        status = "hit"
    elif elapsed:
        status = "missed"
    else:
        status = "pending"

    return RecommendationOutcome(
        rec_id=rec.rec_id, symbol=rec.symbol, current_price=current_price,
        target_price=target, pct_to_target=pct_to_target,
        status=status, days_held=days_held, last_checked=last_checked,
    )


# ── Target-hit confidence ─────────────────────────────────────────────────────
# Blends four signals into a 0-100 confidence that the consensus target is
# reached. It is a heuristic estimate, NOT a guarantee — the track-record term
# grows more meaningful as resolved outcomes accumulate.
_CONF_WEIGHTS = {"proximity": 0.40, "consensus": 0.25, "momentum": 0.20, "track": 0.15}


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def estimate_confidence(
    consensus: ConsensusOut,
    current_price: Optional[float],
    ret_3m: Optional[float],
    hit_rate: Optional[float],
    resolved: int,
) -> Optional[Confidence]:
    """Confidence the consensus target is hit. None when there's no directional
    target/price to assess against (e.g. a hold, or missing data)."""
    target = consensus.avg_target
    direction = ("buy" if consensus.consensus_score > 0
                 else "sell" if consensus.consensus_score < 0 else "hold")
    if direction == "hold" or not target or target <= 0 or not current_price or current_price <= 0:
        return None

    # 1. Proximity: how far price must still move toward the target.
    if direction == "buy":
        needed = max(0.0, (target - current_price) / current_price * 100)
        already = current_price >= target
    else:
        needed = max(0.0, (current_price - target) / current_price * 100)
        already = current_price <= target
    proximity = 100.0 if already else _clamp(100 - needed * 2.0)

    # 2. Consensus: share of analysts aligned with the direction.
    total = consensus.total_count or 1
    aligned = consensus.buy_count if direction == "buy" else consensus.sell_count
    consensus_comp = _clamp(aligned / total * 100)

    # 3. Momentum: recent 3-month move in the recommended direction.
    m = ret_3m or 0.0
    momentum = _clamp(50 + (m if direction == "buy" else -m) * 2.0)

    # 4. Track record: realized hit-rate so far (neutral until data accrues).
    track = _clamp(hit_rate * 100) if (hit_rate is not None and resolved > 0) else 50.0

    score = round(
        proximity * _CONF_WEIGHTS["proximity"]
        + consensus_comp * _CONF_WEIGHTS["consensus"]
        + momentum * _CONF_WEIGHTS["momentum"]
        + track * _CONF_WEIGHTS["track"], 1)
    label = "High" if score >= 70 else "Medium" if score >= 45 else "Low"

    gap_txt = ("already above target" if (direction == "buy" and already)
               else "already below target" if (direction == "sell" and already)
               else f"{needed:.0f}% move needed to target")
    track_txt = (f"{hit_rate*100:.0f}% of {resolved} resolved calls hit so far"
                 if resolved > 0 else "limited target history so far")
    rationale = (f"{label} confidence: {gap_txt}; "
                 f"{aligned}/{total} analysts aligned; "
                 f"3-mo momentum {m:+.0f}%; {track_txt}.")

    return Confidence(score=score, label=label, rationale=rationale, components={
        "proximity": round(proximity, 1), "consensus": round(consensus_comp, 1),
        "momentum": round(momentum, 1), "track": round(track, 1),
    })
