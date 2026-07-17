"""Seed the database with realistic DEMO analyst data.

Use this when the live data sources (Yahoo/Finnhub/TipRanks/Morningstar/FMP)
are unreachable — e.g. behind a restrictive egress proxy, rate-limited, or
offline — so the dashboard still renders a fully populated, believable feed
for local development, screenshots, and demos.

This is SAMPLE DATA, deterministically generated per ticker (stable across
runs). It is clearly not live: every row uses source names suffixed in the
note and the digest prints a DEMO banner. Delete data/recommendations.db (or
run a real `python -m scripts.run_daily`) to replace it with live data.

    python -m scripts.seed_demo            # seed US + India universe
    python -m scripts.seed_demo --reset    # wipe recs/outcomes/profiles first
"""
import argparse
import random
import sqlite3
from datetime import date, timedelta

from app.config import get_settings
from app.models import AnalystRecommendation, RecommendationOutcome
from app.store import RecommendationStore
from app.themes import all_tickers

# Known company names; tickers not listed fall back to a cleaned symbol.
_NAMES = {
    "NVDA": "NVIDIA Corporation", "MSFT": "Microsoft Corporation",
    "GOOGL": "Alphabet Inc.", "META": "Meta Platforms, Inc.",
    "AMZN": "Amazon.com, Inc.", "PLTR": "Palantir Technologies Inc.",
    "AMD": "Advanced Micro Devices", "SNOW": "Snowflake Inc.",
    "AI": "C3.ai, Inc.", "CRM": "Salesforce, Inc.", "NOW": "ServiceNow, Inc.",
    "AVGO": "Broadcom Inc.", "TSM": "Taiwan Semiconductor", "INTC": "Intel Corporation",
    "MU": "Micron Technology", "QCOM": "Qualcomm Incorporated", "ASML": "ASML Holding",
    "AMAT": "Applied Materials", "LRCX": "Lam Research", "TXN": "Texas Instruments",
    "ARM": "Arm Holdings", "JPM": "JPMorgan Chase & Co.", "BAC": "Bank of America",
    "WFC": "Wells Fargo & Co.", "GS": "Goldman Sachs Group", "MS": "Morgan Stanley",
    "C": "Citigroup Inc.", "BLK": "BlackRock, Inc.", "V": "Visa Inc.",
    "MA": "Mastercard Inc.", "AXP": "American Express", "ENPH": "Enphase Energy",
    "FSLR": "First Solar", "SEDG": "SolarEdge Technologies", "NEE": "NextEra Energy",
    "PLUG": "Plug Power", "RUN": "Sunrun Inc.", "BE": "Bloom Energy",
    "TSLA": "Tesla, Inc.", "FLNC": "Fluence Energy", "DLR": "Digital Realty Trust",
    "EQIX": "Equinix, Inc.", "VRT": "Vertiv Holdings", "ANET": "Arista Networks",
    "SMCI": "Super Micro Computer", "RIVN": "Rivian Automotive", "LCID": "Lucid Group",
    "NIO": "NIO Inc.", "F": "Ford Motor Company", "GM": "General Motors",
    "ADBE": "Adobe Inc.", "ORCL": "Oracle Corporation", "DDOG": "Datadog, Inc.",
    "NET": "Cloudflare, Inc.",
    # India
    "TCS.NS": "Tata Consultancy Services", "INFY.NS": "Infosys Ltd",
    "WIPRO.NS": "Wipro Ltd", "HCLTECH.NS": "HCL Technologies", "TECHM.NS": "Tech Mahindra",
    "LTIM.NS": "LTIMindtree", "HDFCBANK.NS": "HDFC Bank", "ICICIBANK.NS": "ICICI Bank",
    "SBIN.NS": "State Bank of India", "KOTAKBANK.NS": "Kotak Mahindra Bank",
    "AXISBANK.NS": "Axis Bank", "INDUSINDBK.NS": "IndusInd Bank",
    "HINDUNILVR.NS": "Hindustan Unilever", "ITC.NS": "ITC Ltd",
    "NESTLEIND.NS": "Nestle India", "BRITANNIA.NS": "Britannia Industries",
    "DABUR.NS": "Dabur India", "TATACONSUM.NS": "Tata Consumer Products",
    "MARUTI.NS": "Maruti Suzuki", "TATAMOTORS.NS": "Tata Motors",
    "M&M.NS": "Mahindra & Mahindra", "BAJAJ-AUTO.NS": "Bajaj Auto",
    "EICHERMOT.NS": "Eicher Motors", "HEROMOTOCO.NS": "Hero MotoCorp",
    "SUNPHARMA.NS": "Sun Pharmaceutical", "DRREDDY.NS": "Dr. Reddy's Labs",
    "CIPLA.NS": "Cipla Ltd", "DIVISLAB.NS": "Divi's Laboratories", "LUPIN.NS": "Lupin Ltd",
    "RELIANCE.NS": "Reliance Industries", "ONGC.NS": "ONGC Ltd", "NTPC.NS": "NTPC Ltd",
    "POWERGRID.NS": "Power Grid Corp", "ADANIGREEN.NS": "Adani Green Energy",
    "TATAPOWER.NS": "Tata Power", "LT.NS": "Larsen & Toubro", "ADANIPORTS.NS": "Adani Ports",
    "ULTRACEMCO.NS": "UltraTech Cement", "SIEMENS.NS": "Siemens India",
    "GRASIM.NS": "Grasim Industries", "BHARTIARTL.NS": "Bharti Airtel", "IDEA.NS": "Vodafone Idea",
}

_FIRMS = ["Morgan Stanley", "Goldman Sachs", "JPMorgan", "UBS", "Barclays",
          "Wedbush", "Citi", "BofA Securities", "Jefferies", "Mizuho",
          "Deutsche Bank", "Evercore ISI", "Piper Sandler", "TD Cowen"]
_BUYERS = ["Vanguard Group", "BlackRock", "State Street", "Fidelity (FMR)",
           "T. Rowe Price", "Geode Capital", "Capital Group"]


def _name(sym: str) -> str:
    return _NAMES.get(sym, sym.replace(".NS", "").replace("-", " ").title())


def _base_price(sym: str, rng: random.Random) -> float:
    """Plausible price; INR (hundreds–thousands) for .NS, USD otherwise."""
    if sym.endswith(".NS"):
        return round(rng.uniform(250, 4200), 2)
    return round(rng.uniform(28, 950), 2)


def _split(total: int, parts: int, rng: random.Random) -> list[int]:
    """Split `total` into `parts` non-negative ints summing to total."""
    if parts <= 1 or total <= 0:
        return [total] + [0] * (parts - 1)
    cuts = sorted(rng.randint(0, total) for _ in range(parts - 1))
    bounds = [0] + cuts + [total]
    return [bounds[i + 1] - bounds[i] for i in range(parts)]


def _seed_symbol(store: RecommendationStore, sym: str, today: date) -> None:
    rng = random.Random(f"alphafunds::{sym}")
    name = _name(sym)
    price = _base_price(sym, rng)

    # Overall stance: ~70% bullish, ~20% mixed, ~10% bearish.
    roll = rng.random()
    if roll < 0.70:
        buy, hold, sell = rng.randint(14, 34), rng.randint(2, 8), rng.randint(0, 3)
        upside = rng.uniform(0.06, 0.28)
    elif roll < 0.90:
        buy, hold, sell = rng.randint(6, 13), rng.randint(5, 12), rng.randint(2, 6)
        upside = rng.uniform(-0.04, 0.12)
    else:
        buy, hold, sell = rng.randint(1, 5), rng.randint(4, 9), rng.randint(6, 14)
        upside = rng.uniform(-0.18, 0.02)

    target = round(price * (1 + upside), 2)
    entry_date = today.isoformat()

    # Aggregate counts spread across the three counting sources (summed by the
    # consensus engine), each carrying the consensus target on its buy row.
    main_rec_id = None
    buy_split, hold_split, sell_split = (
        _split(buy, 3, rng), _split(hold, 3, rng), _split(sell, 3, rng))
    for i, src in enumerate(("yahoo", "finnhub", "tipranks")):
        b, h, s = buy_split[i], hold_split[i], sell_split[i]
        for action, cnt in (("buy", b), ("hold", h), ("sell", s)):
            if cnt <= 0:
                continue
            rid = store.add_recommendation(AnalystRecommendation(
                symbol=sym, source=src, action=action, entry_date=entry_date,
                entry_price=price, count=cnt,
                target_price=target if action == "buy" else None,
                note="[DEMO sample data]",
            ))
            if action == "buy" and rid and main_rec_id is None:
                main_rec_id = rid

    # A few named-firm calls (shown on expand; not summed into counts).
    for firm in rng.sample(_FIRMS, k=rng.randint(2, 4)):
        ft = round(target * rng.uniform(0.92, 1.10), 2)
        raised = rng.random() < 0.6
        note = (f"{firm} raised PT to ${ft:g}" if raised
                else f"{firm} reiterated, PT ${ft:g}") + " [DEMO]"
        store.add_recommendation(AnalystRecommendation(
            symbol=sym, source="yahoo_upgrades", firm=firm,
            action="buy" if (buy >= sell) else "hold",
            entry_date=entry_date, entry_price=price, target_price=ft,
            count=1, note=note,
        ))

    # Profile: company name, trailing returns, ownership.
    returns = {
        "one_month": round(rng.uniform(-9, 12), 2),
        "three_month": round(rng.uniform(-15, 28), 2),
        "six_month": round(rng.uniform(-22, 45), 2),
        "twelve_month": round(rng.uniform(-30, 90), 2),
    }
    ownership = {
        "inst_pct": round(rng.uniform(48, 92), 1),
        "fund_holders": rng.randint(900, 6200),
        "top_buyer": rng.choice(_BUYERS),
        "top_buyer_change": round(rng.uniform(0.3, 9.0), 1),
    }
    store.upsert_profile(sym, name, returns, ownership=ownership)

    # Current outcome for the main buy rec: drives current price + hit/miss badge.
    if main_rec_id is not None:
        # ~30% already hit (price reached target), rest pending; a few resolved
        # historical misses so the leaderboard hit-rate has something to rank.
        hit = rng.random() < 0.30
        cur = round(target * rng.uniform(1.0, 1.06), 2) if hit else price
        pct = round((cur - target) / target * 100, 2)
        store.upsert_outcome(RecommendationOutcome(
            rec_id=main_rec_id, symbol=sym, current_price=cur,
            target_price=target, pct_to_target=pct,
            status="hit" if hit else "pending",
            days_held=rng.randint(12, 220), last_checked=today.isoformat(),
        ))


def _reset(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    for tbl in ("outcomes", "recommendations", "profiles"):
        try:
            con.execute(f"DELETE FROM {tbl}")
        except sqlite3.OperationalError:
            pass
    con.execute("DELETE FROM job_lock WHERE job_id='daily'")
    con.commit()
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed demo analyst data.")
    ap.add_argument("--reset", action="store_true",
                    help="wipe recommendations/outcomes/profiles first")
    args = ap.parse_args()

    settings = get_settings()
    store = RecommendationStore(settings.recommendations_db_path)
    if args.reset:
        _reset(settings.recommendations_db_path)

    today = date.today()
    symbols = all_tickers("us") + all_tickers("in")
    for sym in symbols:
        _seed_symbol(store, sym, today)

    print("=" * 60)
    print("  DEMO DATA SEEDED  (sample analyst data — not live)")
    print("=" * 60)
    print(f"  Symbols seeded : {len(symbols)} ({len(all_tickers('us'))} US, "
          f"{len(all_tickers('in'))} India)")
    print(f"  Database       : {settings.recommendations_db_path}")
    print("  Start the app  : uvicorn app.main:app --port 8100")
    print("  Replace w/ live: delete the .db and run scripts.run_daily")
    print("=" * 60)


if __name__ == "__main__":
    main()
