"""Fund Tracker API router.

Routes (all under /api/funds):
  GET    /              list user's tracked funds with cached metrics (login required)
  POST   /              add a fund to portfolio (login required)
  DELETE /{symbol}      remove a fund from portfolio (login required)
  GET    /compare       compare two funds ?a=SPY&b=QQQ (public)
  GET    /{symbol}      fund detail: metrics + holdings + sectors (public)
  POST   /{symbol}/reindex  rebuild RAG index for a fund (public, background)
"""
import logging
import re
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.auth import get_current_user
from app.config import get_settings
from app.fund_data import get_fund_info, get_fund_holdings, get_fund_performance
from app.models import (
    FundAddRequest,
    FundCompareResult,
    FundDetail,
    FundHolding,
    FundMetrics,
    FundPortfolioItem,
    normalize_symbol,
)
from app.store import RecommendationStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/funds", tags=["funds"])

_settings = get_settings()
_store = RecommendationStore(_settings.recommendations_db_path)

# ── name normalisation for overlap matching ───────────────────────────────────

_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "the", "ltd", "limited", "plc", "sa", "nv", "ag",
    "holdings", "group", "trust", "reit", "etf", "fund", "class",
})


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    tokens = [t for t in s.split() if t and t not in _SUFFIXES]
    return " ".join(tokens) or s.strip()


# ── metrics builder ───────────────────────────────────────────────────────────

def _build_metrics(symbol: str) -> Optional[FundMetrics]:
    info = get_fund_info(symbol)
    if info is None:
        return None
    perf = get_fund_performance(symbol) or {}
    return FundMetrics(
        symbol=symbol,
        name=info.get("name", symbol),
        category=info.get("category"),
        expense_ratio=info.get("expense_ratio"),
        inception_date=info.get("inception_date") or perf.get("inception_date"),
        years_since_inception=perf.get("years_since_inception"),
        since_inception_cagr=perf.get("since_inception_cagr"),
        total_return_pct=perf.get("total_return_pct"),
        cagr_1y=perf.get("cagr_1y"),
        cagr_3y=perf.get("cagr_3y"),
        cagr_5y=perf.get("cagr_5y"),
    )


# ── overlap logic ─────────────────────────────────────────────────────────────

def _compare_holdings(h1: List[dict], h2: List[dict]) -> dict:
    """Match holdings by ticker (preferred) then normalised name."""

    def key(h: dict) -> str:
        return h.get("ticker") or _norm(h.get("name", ""))

    map1 = {key(h): h for h in h1 if key(h)}
    map2 = {key(h): h for h in h2 if key(h)}

    shared_keys = set(map1) & set(map2)
    shared = sorted(
        [
            {
                "name": map1[k].get("name", k),
                "ticker": map1[k].get("ticker"),
                "weight_a": map1[k].get("weight", 0),
                "weight_b": map2[k].get("weight", 0),
            }
            for k in shared_keys
        ],
        key=lambda x: x["weight_a"],
        reverse=True,
    )
    only_a = [h for k, h in map1.items() if k not in shared_keys]
    only_b = [h for k, h in map2.items() if k not in shared_keys]

    return {
        "shared": shared,
        "only_a": only_a,
        "only_b": only_b,
        "overlap_count": len(shared),
        "overlap_weight_a": round(sum(h["weight_a"] for h in shared), 2),
        "overlap_weight_b": round(sum(h["weight_b"] for h in shared), 2),
    }


# ── routes ────────────────────────────────────────────────────────────────────

# NOTE: /compare must be registered BEFORE /{symbol} to avoid FastAPI routing it
# as a symbol lookup for the literal string "compare".

@router.get("/compare", response_model=FundCompareResult)
def compare_funds(
    a: str = Query(..., description="First fund symbol"),
    b: str = Query(..., description="Second fund symbol"),
):
    """Compare two funds side-by-side: metrics + holdings overlap."""
    try:
        sym_a = normalize_symbol(a)
        sym_b = normalize_symbol(b)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))

    metrics_a = _build_metrics(sym_a)
    metrics_b = _build_metrics(sym_b)
    if metrics_a is None:
        raise HTTPException(404, detail=f"No data found for {sym_a}. Check the ticker.")
    if metrics_b is None:
        raise HTTPException(404, detail=f"No data found for {sym_b}. Check the ticker.")

    h1 = get_fund_holdings(sym_a)
    h2 = get_fund_holdings(sym_b)
    ov = _compare_holdings(h1, h2)

    return FundCompareResult(
        fund_a=metrics_a,
        fund_b=metrics_b,
        overlap_count=ov["overlap_count"],
        overlap_weight_a=ov["overlap_weight_a"],
        overlap_weight_b=ov["overlap_weight_b"],
        shared=ov["shared"],
        only_a=[FundHolding(**h) for h in ov["only_a"]],
        only_b=[FundHolding(**h) for h in ov["only_b"]],
    )


@router.get("", response_model=List[FundPortfolioItem])
def list_funds(user: dict = Depends(get_current_user)):
    """List the logged-in user's tracked funds with cached metrics."""
    rows = _store.list_fund_portfolio(user["id"])
    items = []
    for row in rows:
        sym = row["symbol"]
        metrics = _build_metrics(sym)
        items.append(FundPortfolioItem(
            symbol=sym,
            added_at=row["added_at"],
            metrics=metrics,
        ))
    return items


@router.post("", response_model=FundPortfolioItem, status_code=201)
def add_fund(
    req: FundAddRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """Add a fund to the user's portfolio and queue a RAG ingest in the background."""
    sym = req.symbol
    info = get_fund_info(sym)
    if info is None:
        raise HTTPException(404, detail=f"'{sym}' not found. Check the ticker and try again.")

    _store.add_fund(user["id"], sym)
    background_tasks.add_task(_ingest_rag, sym)

    metrics = _build_metrics(sym)
    return FundPortfolioItem(symbol=sym, added_at="just now", metrics=metrics)


@router.delete("/{symbol}", status_code=200)
def remove_fund(symbol: str, user: dict = Depends(get_current_user)):
    """Remove a fund from the user's portfolio."""
    try:
        sym = normalize_symbol(symbol)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    _store.remove_fund(user["id"], sym)
    return {"status": "ok", "symbol": sym}


@router.get("/{symbol}", response_model=FundDetail)
def fund_detail(symbol: str):
    """Public fund detail — metrics, top holdings, sector weights."""
    try:
        sym = normalize_symbol(symbol)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))

    info = get_fund_info(sym)
    if info is None:
        raise HTTPException(404, detail=f"No data found for {sym}. Check the ticker.")

    metrics = _build_metrics(sym)
    notes: List[str] = []
    holdings = [FundHolding(**h) for h in info.get("holdings", [])]
    if not holdings:
        notes.append("Top holdings not available from this data source.")

    return FundDetail(
        metrics=metrics,
        holdings=holdings,
        sector_weights=info.get("sector_weights", {}),
        data_notes=notes,
    )


@router.post("/{symbol}/reindex", status_code=202)
def reindex_fund(symbol: str, background_tasks: BackgroundTasks):
    """Re-build the RAG FAISS index for a fund (runs in background)."""
    try:
        sym = normalize_symbol(symbol)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    background_tasks.add_task(_ingest_rag, sym)
    return {"status": "queued", "symbol": sym}


# ── background helper ─────────────────────────────────────────────────────────

def _ingest_rag(symbol: str) -> None:
    try:
        from app.fund_rag import ingest_fund_docs
        ingest_fund_docs(symbol)
    except Exception as e:
        logger.warning("Background RAG ingest for %s failed: %s", symbol, e)
