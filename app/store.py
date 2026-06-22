"""SQLite persistence for recommendations + their validated outcomes.

Append-only history of analyst calls plus a per-recommendation outcome row that
the daily job refreshes. A module-level write lock keeps concurrent writers
(API background task + scheduler) from stepping on each other; SQLite handles
the rest. Connections are opened per call (cheap, thread-safe).
"""
import os
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from app.models import AnalystRecommendation, RecommendationOutcome

_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recommendations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    source       TEXT NOT NULL,
    firm         TEXT,
    analyst      TEXT,
    action       TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 1,
    note         TEXT,
    url          TEXT,
    target_price REAL,
    entry_price  REAL,
    entry_date   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rec_symbol ON recommendations(symbol);
CREATE INDEX IF NOT EXISTS idx_rec_date   ON recommendations(entry_date);
-- Dedupe key. Uses IFNULL(firm,'') because SQLite treats NULLs as distinct in
-- UNIQUE constraints, which would otherwise let duplicate firm-less rows in.
CREATE UNIQUE INDEX IF NOT EXISTS idx_rec_dedupe
    ON recommendations(symbol, source, IFNULL(firm, ''), action, entry_date);

CREATE TABLE IF NOT EXISTS outcomes (
    rec_id        INTEGER PRIMARY KEY REFERENCES recommendations(id) ON DELETE CASCADE,
    current_price REAL,
    target_price  REAL,
    pct_to_target REAL,
    status        TEXT NOT NULL,
    days_held     INTEGER NOT NULL DEFAULT 0,
    last_checked  TEXT
);

CREATE TABLE IF NOT EXISTS profiles (
    symbol           TEXT PRIMARY KEY,
    company_name     TEXT,
    ret_1m           REAL,
    ret_3m           REAL,
    ret_6m           REAL,
    ret_12m          REAL,
    inst_pct         REAL,
    fund_holders     INTEGER,
    top_buyer        TEXT,
    top_buyer_change REAL,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS watchlist (
    user_id      INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    grp          TEXT NOT NULL DEFAULT 'My Watchlist',
    pin_date     TEXT NOT NULL,   -- day the price was pinned
    pin_price    REAL,            -- price snapshot on the pin day
    company_name TEXT,
    added_at     TEXT,
    PRIMARY KEY (user_id, symbol, grp)
);

CREATE TABLE IF NOT EXISTS job_lock (
    job_id   TEXT PRIMARY KEY,
    last_run TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics_daily (
    day      TEXT PRIMARY KEY,   -- "YYYY-MM-DD"
    hits     INTEGER NOT NULL DEFAULT 0,   -- app page loads that day
    visitors INTEGER NOT NULL DEFAULT 0    -- first-time (new-cookie) visitors
);
"""


class RecommendationStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        self._init_schema()

    # ── connection ────────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after the first release to older DBs."""
        existing = {row["name"] for row in conn.execute(
            "PRAGMA table_info(recommendations)"
        )}
        for col in ("note", "url"):
            if col not in existing:
                conn.execute(f"ALTER TABLE recommendations ADD COLUMN {col} TEXT")
        # profiles ownership columns (added after first release)
        prof_cols = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)")}
        for col, typ in (("inst_pct", "REAL"), ("fund_holders", "INTEGER"),
                         ("top_buyer", "TEXT"), ("top_buyer_change", "REAL")):
            if col not in prof_cols:
                conn.execute(f"ALTER TABLE profiles ADD COLUMN {col} {typ}")
        # Per-user watchlists: the old table was keyed by (symbol, grp) with no
        # owner. SQLite can't ALTER a primary key, so rebuild. Pre-production
        # rows weren't owned by anyone, so they are dropped (not migrated).
        wl_cols = {row["name"] for row in conn.execute("PRAGMA table_info(watchlist)")}
        if wl_cols and "user_id" not in wl_cols:
            conn.execute("DROP TABLE watchlist")
            conn.executescript(_SCHEMA)
        # Add role column to users table if missing (older databases).
        user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if user_cols and "role" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    # ── writes ──────────────────────────────────────────────────────────────────
    def add_recommendation(self, rec: AnalystRecommendation) -> Optional[int]:
        """Insert a recommendation. Returns the new row id, or None if it was a
        duplicate (same symbol/source/firm/action/entry_date) — this is what
        stops a same-day re-run from double-counting."""
        with _write_lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO recommendations
                   (symbol, source, firm, analyst, action, count, note, url,
                    target_price, entry_price, entry_date, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.symbol, rec.source, rec.firm, rec.analyst, rec.action,
                    rec.count, rec.note, rec.url, rec.target_price,
                    rec.entry_price, rec.entry_date, date.today().isoformat(),
                ),
            )
            return cur.lastrowid if cur.rowcount else None

    def upsert_outcome(self, outcome: RecommendationOutcome) -> None:
        if outcome.rec_id is None:
            return
        with _write_lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO outcomes
                   (rec_id, current_price, target_price, pct_to_target,
                    status, days_held, last_checked)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(rec_id) DO UPDATE SET
                     current_price=excluded.current_price,
                     target_price=excluded.target_price,
                     pct_to_target=excluded.pct_to_target,
                     status=excluded.status,
                     days_held=excluded.days_held,
                     last_checked=excluded.last_checked""",
                (
                    outcome.rec_id, outcome.current_price, outcome.target_price,
                    outcome.pct_to_target, outcome.status, outcome.days_held,
                    outcome.last_checked,
                ),
            )

    # ── reads ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_rec(row: sqlite3.Row) -> AnalystRecommendation:
        return AnalystRecommendation(
            rec_id=row["id"], symbol=row["symbol"], source=row["source"],
            firm=row["firm"], analyst=row["analyst"], action=row["action"],
            count=row["count"], note=row["note"], url=row["url"],
            target_price=row["target_price"],
            entry_price=row["entry_price"], entry_date=row["entry_date"],
        )

    def list_recent(self, days: int = 1) -> List[AnalystRecommendation]:
        cutoff = (date.today() - timedelta(days=max(0, days - 1))).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE entry_date >= ? "
                "ORDER BY entry_date DESC, symbol ASC",
                (cutoff,),
            ).fetchall()
        return [self._row_to_rec(r) for r in rows]

    def list_for_symbol(self, symbol: str) -> List[AnalystRecommendation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE symbol = ? "
                "ORDER BY entry_date DESC",
                (symbol,),
            ).fetchall()
        return [self._row_to_rec(r) for r in rows]

    def all_symbols(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM recommendations ORDER BY symbol"
            ).fetchall()
        return [r["symbol"] for r in rows]

    def all_recommendations(self) -> List[AnalystRecommendation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations ORDER BY entry_date DESC"
            ).fetchall()
        return [self._row_to_rec(r) for r in rows]

    def pending_recommendations(self) -> List[AnalystRecommendation]:
        """Recs with no outcome yet, or whose outcome is still 'pending'.
        These are the ones the daily validation pass needs to re-check."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.* FROM recommendations r
                   LEFT JOIN outcomes o ON o.rec_id = r.id
                   WHERE o.rec_id IS NULL OR o.status = 'pending'
                   ORDER BY r.symbol""",
            ).fetchall()
        return [self._row_to_rec(r) for r in rows]

    def latest_outcome(self, symbol: str) -> Optional[dict]:
        """Most recently checked outcome for a symbol (any rec), as a dict."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT o.* FROM outcomes o
                   JOIN recommendations r ON r.id = o.rec_id
                   WHERE r.symbol = ?
                   ORDER BY o.last_checked DESC LIMIT 1""",
                (symbol,),
            ).fetchone()
        return dict(row) if row else None

    # ── profiles (company name + returns) ───────────────────────────────────────
    def upsert_profile(self, symbol: str, company_name: Optional[str],
                       returns: dict, ownership: Optional[dict] = None) -> None:
        own = ownership or {}
        with _write_lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO profiles
                   (symbol, company_name, ret_1m, ret_3m, ret_6m, ret_12m,
                    inst_pct, fund_holders, top_buyer, top_buyer_change, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     company_name=excluded.company_name, ret_1m=excluded.ret_1m,
                     ret_3m=excluded.ret_3m, ret_6m=excluded.ret_6m,
                     ret_12m=excluded.ret_12m, inst_pct=excluded.inst_pct,
                     fund_holders=excluded.fund_holders, top_buyer=excluded.top_buyer,
                     top_buyer_change=excluded.top_buyer_change,
                     updated_at=excluded.updated_at""",
                (symbol, company_name, returns.get("one_month"),
                 returns.get("three_month"), returns.get("six_month"),
                 returns.get("twelve_month"), own.get("inst_pct"),
                 own.get("fund_holders"), own.get("top_buyer"),
                 own.get("top_buyer_change"), date.today().isoformat()),
            )

    def get_profile(self, symbol: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profiles WHERE symbol = ?", (symbol,)
            ).fetchone()
        return dict(row) if row else None

    # ── users ─────────────────────────────────────────────────────────────────
    def create_user(self, email: str, password_hash: str,
                    display_name: Optional[str]) -> int:
        """Insert a user, returning its new id. Raises ValueError if the email
        is already registered."""
        with _write_lock, self._connect() as conn:
            try:
                cur = conn.execute(
                    """INSERT INTO users (email, password_hash, display_name, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (email, password_hash, display_name, date.today().isoformat()),
                )
            except sqlite3.IntegrityError:
                raise ValueError("An account with this email already exists.")
            return int(cur.lastrowid)

    def get_user_by_email(self, email: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, uid: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(row) if row else None

    def set_password_hash(self, uid: int, password_hash: str) -> None:
        with _write_lock, self._connect() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (password_hash, uid))

    def set_user_role(self, uid: int, role: str) -> None:
        with _write_lock, self._connect() as conn:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))

    def list_users(self) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, email, display_name, role, created_at FROM users ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def ensure_admin(self, email: str) -> None:
        """Promote a user to admin by email — idempotent, used at startup."""
        if not email:
            return
        with _write_lock, self._connect() as conn:
            conn.execute(
                "UPDATE users SET role = 'admin' WHERE email = ? COLLATE NOCASE AND role != 'admin'",
                (email,),
            )

    # ── password reset tokens ────────────────────────────────────────────────────
    def create_reset_token(self, user_id: int, ttl_hours: int = 1) -> str:
        """Generate a secure token valid for ttl_hours. Old tokens for the same
        user are purged first so only one active link exists at a time."""
        token = secrets.token_urlsafe(32)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        ).isoformat()
        with _write_lock, self._connect() as conn:
            conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,))
            conn.execute(
                "INSERT INTO password_reset_tokens (token, user_id, expires_at, used) VALUES (?, ?, ?, 0)",
                (token, user_id, expires_at),
            )
        return token

    def consume_reset_token(self, token: str) -> Optional[int]:
        """Validate and mark a reset token as used. Returns user_id on success, None otherwise."""
        now = datetime.now(timezone.utc).isoformat()
        with _write_lock, self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at, used FROM password_reset_tokens WHERE token = ?",
                (token,),
            ).fetchone()
            if not row or row["used"] or row["expires_at"] < now:
                return None
            conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (token,))
            return int(row["user_id"])

    # ── traffic metrics + admin stats ────────────────────────────────────────────
    def bump_metric(self, day: str, new_visitor: bool) -> None:
        """Record one app page-load for `day`; flag new_visitor for unique counts."""
        nv = 1 if new_visitor else 0
        with _write_lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO metrics_daily (day, hits, visitors) VALUES (?, 1, ?)
                   ON CONFLICT(day) DO UPDATE SET hits = hits + 1, visitors = visitors + ?""",
                (day, nv, nv),
            )

    def daily_metrics(self, days: int = 14) -> List[dict]:
        """Most recent `days` of traffic, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT day, hits, visitors FROM metrics_daily ORDER BY day DESC LIMIT ?",
                (days,),
            ).fetchall()
        return [dict(r) for r in rows]

    def admin_stats(self) -> dict:
        """Aggregate counts for the admin dashboard, in a handful of queries."""
        today = date.today()
        d7 = (today - timedelta(days=7)).isoformat()
        d30 = (today - timedelta(days=30)).isoformat()
        with self._connect() as conn:
            def scalar(sql, args=()):
                row = conn.execute(sql, args).fetchone()
                return row[0] if row else 0

            users_total = scalar("SELECT COUNT(*) FROM users")
            by_role = {r["role"]: r["n"] for r in conn.execute(
                "SELECT role, COUNT(*) AS n FROM users GROUP BY role")}
            signups_7d = scalar("SELECT COUNT(*) FROM users WHERE created_at >= ?", (d7,))
            signups_30d = scalar("SELECT COUNT(*) FROM users WHERE created_at >= ?", (d30,))

            wl_total = scalar("SELECT COUNT(*) FROM watchlist")
            wl_users = scalar("SELECT COUNT(DISTINCT user_id) FROM watchlist")
            top_pins = [dict(r) for r in conn.execute(
                """SELECT symbol, COUNT(*) AS pins FROM watchlist
                   GROUP BY symbol ORDER BY pins DESC, symbol LIMIT 10""")]

            recs_total = scalar("SELECT COUNT(*) FROM recommendations")
            symbols_covered = scalar("SELECT COUNT(DISTINCT symbol) FROM recommendations")
            outcomes = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM outcomes GROUP BY status")}

            hits_total = scalar("SELECT COALESCE(SUM(hits), 0) FROM metrics_daily")
            visitors_total = scalar("SELECT COALESCE(SUM(visitors), 0) FROM metrics_daily")
            hits_7d = scalar("SELECT COALESCE(SUM(hits), 0) FROM metrics_daily WHERE day >= ?", (d7,))

        resolved = outcomes.get("hit", 0) + outcomes.get("missed", 0)
        hit_rate = round(outcomes.get("hit", 0) / resolved * 100, 1) if resolved else None
        return {
            "users": {"total": users_total, "by_role": by_role,
                      "signups_7d": signups_7d, "signups_30d": signups_30d},
            "engagement": {"watchlist_pins": wl_total, "users_with_pins": wl_users,
                           "top_pinned": top_pins},
            "coverage": {"recommendations": recs_total, "symbols": symbols_covered,
                         "outcomes": outcomes, "hit_rate_pct": hit_rate},
            "traffic": {"hits_total": hits_total, "visitors_total": visitors_total,
                        "hits_7d": hits_7d, "daily": self.daily_metrics(14)},
        }

    # ── watchlist ───────────────────────────────────────────────────────────────
    def add_watchlist(self, user_id: int, symbol: str, group: str, pin_date: str,
                      pin_price: Optional[float], company_name: Optional[str]) -> bool:
        """Pin a stock to a user's watchlist group. Returns False if already
        pinned (so the original pin date/price is preserved)."""
        with _write_lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO watchlist
                   (user_id, symbol, grp, pin_date, pin_price, company_name, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, symbol, group, pin_date, pin_price, company_name,
                 date.today().isoformat()),
            )
            return bool(cur.rowcount)

    def remove_watchlist(self, user_id: int, symbol: str, group: str) -> bool:
        with _write_lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND symbol = ? AND grp = ?",
                (user_id, symbol, group))
            return bool(cur.rowcount)

    def list_watchlist(self, user_id: int, group: Optional[str] = None) -> List[dict]:
        with self._connect() as conn:
            if group:
                rows = conn.execute(
                    """SELECT * FROM watchlist WHERE user_id = ? AND grp = ?
                       ORDER BY added_at DESC""",
                    (user_id, group)).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM watchlist WHERE user_id = ?
                       ORDER BY grp, added_at DESC""", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def watchlist_groups(self, user_id: int) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT grp FROM watchlist WHERE user_id = ? ORDER BY grp",
                (user_id,)).fetchall()
        return [r["grp"] for r in rows]

    def outcome_counts(self, symbol: str) -> dict:
        """{status: count} of resolved outcomes for a symbol — feeds hit-rate."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT o.status, COUNT(*) AS n FROM outcomes o
                   JOIN recommendations r ON r.id = o.rec_id
                   WHERE r.symbol = ? GROUP BY o.status""",
                (symbol,),
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def latest_outcomes_all(self) -> dict:
        """Most recent outcome per symbol in one query: {symbol: dict}.
        Use this instead of calling latest_outcome(symbol) in a loop."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT r.symbol, o.current_price, o.target_price, o.pct_to_target,
                       o.status, o.days_held, o.last_checked
                FROM outcomes o
                JOIN recommendations r ON r.id = o.rec_id
                WHERE o.rec_id IN (
                    SELECT o2.rec_id
                    FROM outcomes o2
                    JOIN recommendations r2 ON r2.id = o2.rec_id
                    GROUP BY r2.symbol
                    HAVING o2.last_checked = MAX(o2.last_checked)
                )
            """).fetchall()
        return {row["symbol"]: dict(row) for row in rows}

    def all_profiles(self) -> dict:
        """All stored profiles in one query: {symbol: dict}."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM profiles").fetchall()
        return {row["symbol"]: dict(row) for row in rows}

    def claim_daily_job(self, today: str) -> bool:
        """Returns True if this call wins the right to run today's daily job.
        False if the job already ran today — safe across threads and OS processes."""
        with _write_lock, self._connect() as conn:
            row = conn.execute(
                "SELECT last_run FROM job_lock WHERE job_id = 'daily'"
            ).fetchone()
            if row and row["last_run"] == today:
                return False
            conn.execute(
                "INSERT OR REPLACE INTO job_lock (job_id, last_run) VALUES ('daily', ?)",
                (today,),
            )
            return True

    def outcome_counts_all(self) -> dict:
        """All resolved outcome counts in one query: {symbol: {status: count}}.
        Use this instead of calling outcome_counts(symbol) in a loop."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.symbol, o.status, COUNT(*) AS n FROM outcomes o
                   JOIN recommendations r ON r.id = o.rec_id
                   GROUP BY r.symbol, o.status"""
            ).fetchall()
        result: dict = {}
        for row in rows:
            result.setdefault(row["symbol"], {})[row["status"]] = row["n"]
        return result

    def all_recommendations_by_symbol(self) -> dict:
        """All recommendations grouped by symbol in a single query.
        Use this instead of calling list_for_symbol(sym) in a loop."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations ORDER BY entry_date DESC"
            ).fetchall()
        by_symbol: dict = {}
        for row in rows:
            rec = self._row_to_rec(row)
            by_symbol.setdefault(rec.symbol, []).append(rec)
        return by_symbol
