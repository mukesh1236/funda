"""Regenerate the table reference inside docs/schemas/entities.md.

The generated region is derived by EXECUTING app.store._SCHEMA into an
in-memory SQLite database and reading PRAGMA metadata, so it cannot disagree
with the real schema. Hand-written prose (what each table is for, how they
relate) lives outside the markers and is never touched.

    python scripts/export_schema_doc.py           # rewrite the generated block
    python scripts/export_schema_doc.py --check   # exit 1 if it is out of date

Columns added later by RecommendationStore._migrate() are included only when
they are also present in _SCHEMA; the base schema here is the source of truth
for a fresh database, which is what _migrate() converges older databases to.
"""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "schemas" / "entities.md"
BEGIN = "<!-- BEGIN GENERATED: scripts/export_schema_doc.py -->"
END = "<!-- END GENERATED -->"


def _load_schema_sql() -> str:
    sys.path.insert(0, str(ROOT))
    from app.store import _SCHEMA
    return _SCHEMA


def _column_comments(sql: str) -> dict:
    """{table: {column: comment}} from `-- ...` trailing a column definition."""
    out: dict = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        table, body = m.group(1), m.group(2)
        for line in body.splitlines():
            cm = re.match(r"\s*(\w+)\s+[^,]*?,?\s*--\s*(.+?)\s*$", line)
            if cm:
                out.setdefault(table, {})[cm.group(1)] = cm.group(2)
    return out


def render() -> str:
    sql = _load_schema_sql()
    comments = _column_comments(sql)
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    parts = [f"{len(tables)} tables, generated from `app/store.py::_SCHEMA`.\n"]
    for t in tables:
        parts.append(f"### `{t}`\n")
        parts.append("| column | type | null | default | notes |")
        parts.append("|---|---|---|---|---|")
        for _cid, name, typ, notnull, dflt, pk in conn.execute(f"PRAGMA table_info({t})"):
            notes = []
            if pk:
                notes.append("PK")
            if name in comments.get(t, {}):
                notes.append(comments[t][name].replace("|", "\\|"))
            parts.append(
                f"| `{name}` | {typ or ''} | {'no' if notnull or pk else 'yes'} "
                f"| {('`' + str(dflt) + '`') if dflt is not None else ''} "
                f"| {'; '.join(notes)} |")
        fks = list(conn.execute(f"PRAGMA foreign_key_list({t})"))
        if fks:
            parts.append("\nForeign keys: " + ", ".join(
                f"`{r[3]}` -> `{r[2]}.{r[4]}`" + (f" (on delete {r[6].lower()})"
                if r[6] and r[6] != "NO ACTION" else "") for r in fks))
        idx = []
        for _seq, iname, unique, origin, _partial in conn.execute(f"PRAGMA index_list({t})"):
            if origin == "pk":
                continue
            if origin == "c":
                # CREATE INDEX: quote the written column list. PRAGMA index_info
                # reports a NULL name for expression terms such as IFNULL(firm, ''),
                # which is exactly where the dedupe indexes matter most.
                ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name=?",
                                   (iname,)).fetchone()[0]
                cols = re.search(r"ON\s+\w+\s*\((.*)\)\s*$", " ".join(ddl.split()), re.S).group(1)
                idx.append(f"{'unique ' if unique else ''}({cols})")
            else:
                cols = ", ".join(c[2] for c in conn.execute(f"PRAGMA index_info({iname})"))
                idx.append(f"unique ({cols}) [UNIQUE constraint]")
        if idx:
            parts.append("\nIndexes: " + "; ".join(f"`{i}`" for i in idx))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def splice(doc: str, block: str) -> str:
    if BEGIN not in doc or END not in doc:
        raise SystemExit(f"{OUT.name} is missing the {BEGIN} / {END} markers")
    head, rest = doc.split(BEGIN, 1)
    _old, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n\n{block}\n{END}{tail}"


def is_current() -> bool:
    doc = OUT.read_text(encoding="utf-8")
    return splice(doc, render()) == doc


def main(argv: list) -> int:
    if "--check" in argv:
        ok = is_current()
        if not ok:
            print("docs/schemas/entities.md generated block is out of date; "
                  "run: python scripts/export_schema_doc.py")
        return 0 if ok else 1
    doc = OUT.read_text(encoding="utf-8")
    OUT.write_text(splice(doc, render()), encoding="utf-8")
    print(f"rewrote generated block in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
