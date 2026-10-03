"""The product tour points at real elements by selector. If a refactor renames or
removes one, the tour silently falls back to a centred card with nothing to point at,
and nothing else would notice. These tests keep the steps honest."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "styles.css").read_text(encoding="utf-8")


def _steps_block() -> str:
    start = JS.index("const TOUR_STEPS = [")
    return JS[start:JS.index("\n];", start)]


def _groups() -> list:
    """The selector list of each step. Ends at `], title:` because a selector can
    itself contain `]` (an attribute selector like [data-view="funds"])."""
    return re.findall(r"sel:\s*\[(.*?)\]\s*,\s*title:", _steps_block(), re.S)


def _selectors() -> list:
    out = []
    for group in _groups():
        out += re.findall(r"'([^']+)'", group)
    return out


def test_the_tour_has_steps_with_titles_and_bodies():
    block = _steps_block()
    titles = re.findall(r"title:\s*'((?:[^'\\]|\\.)*)'", block)
    bodies = re.findall(r"body:\s*'((?:[^'\\]|\\.)*)'", block)
    assert len(titles) == len(bodies) >= 8
    assert all(t.strip() for t in titles) and all(len(b) > 40 for b in bodies)


def test_no_emoji_in_the_tour_copy():
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", _steps_block())


def test_selectors_are_parsed_completely():
    sels = _selectors()
    assert "#stats" in sels and "#content tr.row" in sels
    assert '.topnav [data-view="funds"]' in sels and '.tabbar [data-view="funds"]' in sels


def test_every_selector_still_exists_in_the_markup():
    """Each id, class and data-view the steps use must appear in index.html or in the
    templates app.js renders (the table rows and highlights are built in JS)."""
    haystack = HTML + JS
    missing = []
    for sel in _selectors():
        for ident in re.findall(r"#([A-Za-z][\w-]*)", sel):
            if f'id="{ident}"' not in haystack:
                missing.append(f"#{ident}")
        for cls in re.findall(r"\.([A-Za-z][\w-]*)", sel):
            if not re.search(rf'class="[^"]*\b{re.escape(cls)}\b', haystack) \
                    and f"'{cls}'" not in haystack and f" {cls}" not in haystack:
                missing.append(f".{cls}")
        for view in re.findall(r'data-view="([\w-]+)"', sel):
            if f'data-view="{view}"' not in HTML:
                missing.append(f"data-view={view}")
    assert not missing, f"tour points at things that no longer exist: {sorted(set(missing))}"


def test_a_phone_fallback_exists_for_every_nav_step():
    """The desktop nav is hidden on a phone, where the tab bar stands in; a nav step
    needs both selectors or it would show centred and unexplained on mobile."""
    assert len(_groups()) >= 10
    for group in _groups():
        if "data-view" in group:
            assert ".topnav" in group and ".tabbar" in group, group


def test_entry_points_exist():
    assert 'id="tourBtn"' in HTML          # header button (desktop)
    assert 'id="tourMenu"' in HTML         # account menu entry (phone)
    assert 'id="welcomeTour"' in HTML      # offer on the welcome popup
    assert "function startTour" in JS and "function endTour" in JS


def test_new_accounts_start_it_but_logins_do_not():
    assert re.search(r"wasSignup\s*=\s*_authMode\s*===\s*'signup'", JS)
    assert re.search(r"if \(wasSignup\)\s*_autoTourForNewAccount\(\)", JS)
    # the flag is per browser and per account, and storage access is guarded
    assert "'tour_done_' + (_currentUser ? _currentUser.id : 'guest')" in JS
    seg = JS[JS.index("function _tourDone"):JS.index("function _autoTourForNewAccount")]
    assert seg.count("try {") >= 2


def test_the_tour_is_keyboard_operable_and_accessible():
    seg = JS[JS.index("async function startTour"):JS.index("function endTour")]
    for needle in ("role=\"dialog\"", "aria-modal=\"true\"", "aria-labelledby=\"tourTitle\"",
                   "'Escape'", "'ArrowRight'", "'ArrowLeft'", "'Tab'"):
        assert needle in seg, needle


def test_tour_styles_use_tokens_only():
    block = CSS[CSS.index("/* ── Product tour"):CSS.index("/* ── Mobile tab bar")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", block)
    assert "var(--scrim)" in block
