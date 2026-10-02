"""The docs/ knowledge base must not drift from the code it describes.

Prose cannot be tested, so the guards cover what can rot silently:

* docs/api/openapi.yaml is GENERATED from the app. A route added or removed without
  regenerating makes it stale. (Compared by route, not by bytes: requirements.txt is
  unpinned, so FastAPI may legitimately reshape schema fragments between versions,
  and a byte comparison would fail CI for reasons that are not documentation drift.)
* The table reference in docs/schemas/entities.md is GENERATED from _SCHEMA.
* Every relative link in the docs must resolve: moving files is exactly how links die.
* The ADR index must list every ADR, and no ADR may be missing from it.
* The repository is public, so no infrastructure identifiers may creep into the docs.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# Everything that links into the knowledge base or is part of it.
MD_FILES = sorted(
    list(DOCS.rglob("*.md")) + [ROOT / n for n in ("CLAUDE.md", "HANDOFF.md", "README.md", "BACKLOG.md")]
)


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestGeneratedDocs:

    def test_openapi_lists_every_route(self):
        mod = _load_script("export_openapi")
        missing, extra = mod.drift()
        assert not missing and not extra, (
            "docs/api/openapi.yaml is out of date. Run: python scripts/export_openapi.py\n"
            f"in app, not in docs: {missing}\nin docs, not in app: {extra}")

    def test_openapi_drift_check_actually_detects_a_change(self, tmp_path):
        """A guard that cannot fail protects nothing: drop a route from a copy of the
        spec and confirm the comparison reports it."""
        import yaml

        mod = _load_script("export_openapi")
        spec = yaml.safe_load(mod.OUT.read_text(encoding="utf-8"))
        victim = next(iter(spec["paths"]))
        del spec["paths"][victim]
        stale = tmp_path / "openapi.yaml"
        stale.write_text(yaml.safe_dump(spec), encoding="utf-8")

        missing, _extra = mod.drift(stale)
        assert any(path == victim for _method, path in missing)

    def test_schema_reference_is_current(self):
        mod = _load_script("export_schema_doc")
        assert mod.is_current(), (
            "the generated block in docs/schemas/entities.md is out of date. "
            "Run: python scripts/export_schema_doc.py")

    def test_schema_reference_covers_every_table(self):
        mod = _load_script("export_schema_doc")
        tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", mod._load_schema_sql())
        text = (DOCS / "schemas" / "entities.md").read_text(encoding="utf-8")
        missing = [t for t in tables if f"### `{t}`" not in text]
        assert not missing, f"tables missing from the generated reference: {missing}"

    def test_every_table_has_a_purpose_in_the_prose(self):
        """The generated block lists columns; the hand-written table says what each
        table is FOR. A new table with no purpose line is half-documented."""
        mod = _load_script("export_schema_doc")
        tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", mod._load_schema_sql())
        text = (DOCS / "schemas" / "entities.md").read_text(encoding="utf-8")
        prose = text.split(mod.BEGIN)[0]
        missing = [t for t in tables if f"`{t}`" not in prose]
        assert not missing, f"add a purpose row for: {missing}"


_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"(?<!\!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _relative_links(md: Path):
    text = _INLINE.sub("", _FENCE.sub("", md.read_text(encoding="utf-8")))
    for target in _LINK.findall(text):
        if re.match(r"^(https?:|mailto:|#)", target):
            continue
        yield target.split("#", 1)[0]


@pytest.mark.parametrize("md", MD_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(md):
    broken = []
    for target in _relative_links(md):
        if not target:
            continue
        if not (md.parent / target).resolve().exists():
            broken.append(target)
    assert not broken, f"{md.relative_to(ROOT)} has broken links: {broken}"


class TestAdrIndex:

    def test_index_lists_every_adr(self):
        adrs = sorted(p.name for p in (DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")
                      if not p.name.startswith("0000"))
        index = (DOCS / "adr" / "README.md").read_text(encoding="utf-8")
        missing = [a for a in adrs if a not in index]
        assert not missing, f"adr/README.md does not list: {missing}"

    def test_adr_numbers_are_unique_and_sequential(self):
        nums = sorted(int(p.name[:4]) for p in (DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")
                      if not p.name.startswith("0000"))
        assert nums == list(range(1, len(nums) + 1)), f"ADR numbering has a gap or reuse: {nums}"

    def test_every_adr_states_status_and_date(self):
        for p in (DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"):
            if p.name.startswith("0000"):
                continue
            text = p.read_text(encoding="utf-8")
            assert re.search(r"^- \*\*Status:\*\*", text, re.M), f"{p.name} has no Status"
            assert re.search(r"^- \*\*Date:\*\*", text, re.M), f"{p.name} has no Date"


def test_no_infrastructure_identifiers_in_public_docs():
    """The repository is public. Railway project/service/trigger IDs are not
    credentials, but there is no reason to publish them; runbooks say 'look it up in
    the dashboard'."""
    uuid = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
    trigger = re.compile(r"\btrig_[A-Za-z0-9]{10,}\b")
    offenders = []
    for md in MD_FILES:
        text = md.read_text(encoding="utf-8")
        if uuid.search(text) or trigger.search(text):
            offenders.append(str(md.relative_to(ROOT)))
    assert not offenders, f"infrastructure identifiers found in: {offenders}"
