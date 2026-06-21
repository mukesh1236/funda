"""Pure aggregation + validation functions. No I/O, no mutation of inputs.

Two responsibilities:
  - compute_consensus: turn many analyst calls on one stock into a single
    rating by counting buys/holds/sells (the user's "add the counts" ask).
  - evaluate_outcome: decide whether a recommendation's target was hit, missed,
    still pending, or expired, given the current price.
"""
import math
from datetime import date
from typing import Dict, List, Optional

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

# Baseline reliability per source when we don't have enough outcome history yet.
# Blended with actual hit rates once ≥5 resolved recommendations exist.
_SOURCE_BASE_QUALITY: Dict[str, float] = {
    "yahoo": 0.55,
    "finnhub": 0.55,
    "tipranks": 0.60,
    "fmp": 0.58,
    "morningstar": 0.65,
    "polygon": 0.57,
}


def _recency_weight(entry_date: Optional[str], today: date, half_life_days: int = 60) -> float:
    """Exponential decay — full weight when fresh, 0.5 after 60 days, ~0.25 after 120."""
    if not entry_date:
        return 1.0
    try:
        days_old = max(0, (today - date.fromisoformat(entry_date)).days)
        return math.exp(-math.log(2) * days_old / half_life_days)
    except (ValueError, TypeError):
        return 1.0


def _source_weight(source: str, actual_hit_rate: Optional[float], resolved: int) -> float:
    """Blend baseline quality with actual hit rate, weighted by sample size."""
    base = _SOURCE_BASE_QUALITY.get(source, 0.5)
    if actual_hit_rate is None or resolved < 5:
        return base
    blend = min(resolved / 20.0, 1.0)   # full blend at 20 resolved calls
    return base * (1 - blend * 0.7) + actual_hit_rate * blend * 0.7


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


def compute_consensus(
    recs: List[AnalystRecommendation],
    source_hit_rates: Optional[Dict[str, float]] = None,
    source_resolved: Optional[Dict[str, int]] = None,
) -> Optional[ConsensusOut]:
    """Aggregate recommendations for ONE symbol into a consensus rating.

    Uses each source's latest snapshot only (see _latest_per_source) so repeated
    daily collection doesn't inflate counts.

    When source_hit_rates is supplied (from historical outcomes), the weighted_score
    applies hit-rate quality × recency decay per source. conviction_score measures
    analyst agreement (0=split, 1=unanimous).
    """
    if not recs:
        return None

    symbol = recs[0].symbol
    recs = _latest_per_source(recs)
    today = date.today()

    counting = [r for r in recs if r.source in COUNTING_SOURCES]
    buy = sum(r.count for r in counting if r.action == "buy")
    hold = sum(r.count for r in counting if r.action == "hold")
    sell = sum(r.count for r in counting if r.action == "sell")
    total = buy + hold + sell

    targets = [r.target_price for r in recs if r.target_price and r.target_price > 0]
    avg_target = round(sum(targets) / len(targets), 2) if targets else None

    sources = sorted({r.source for r in recs if r.source})
    firms = sorted({r.firm for r in recs if r.firm})
    latest = max((r.entry_date for r in recs if r.entry_date), default=None)

    # Conviction: how aligned are analysts? 1.0 = unanimous, 0.5 = evenly split.
    conviction = round(max(buy, sell) / total, 3) if total > 0 else None

    # Weighted score: each source's counts scaled by quality × recency.
    w_buy = w_sell = 0.0
    for r in counting:
        actual_rate = (source_hit_rates or {}).get(r.source)
        resolved = (source_resolved or {}).get(r.source, 0)
        quality = _source_weight(r.source, actual_rate, resolved)
        recency = _recency_weight(r.entry_date, today)
        w = quality * recency
        if r.action == "buy":
            w_buy += r.count * w
        elif r.action == "sell":
            w_sell += r.count * w
    weighted_score = round(w_buy - w_sell, 2) if (w_buy or w_sell) else None

    return ConsensusOut(
        symbol=symbol,
        buy_count=buy,
        hold_count=hold,
        sell_count=sell,
        total_count=total,
        consensus_score=buy - sell,
        weighted_score=weighted_score,
        conviction_score=conviction,
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
