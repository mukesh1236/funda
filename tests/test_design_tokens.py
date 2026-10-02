"""Guards for the design-token system in web/styles.css (see ADR 0011).

Legibility and theme parity are the properties that rot silently: one new
colour picked to look right on a single screen, or a token added to the light
theme and forgotten in the dark one, ships without anyone noticing. So both are
asserted here, in the same spirit as tests/test_cache_bounds.py.
"""
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
CSS = (WEB / "styles.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")

# Tokens that are deliberately the same in both themes (brand constants).
THEME_INVARIANT = {"wa", "on-wa"}


def _strip_comments(s: str) -> str:
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


def _block(src: str, header: str) -> str:
    """Body of the first `{...}` that follows `header` (brace-balanced)."""
    start = src.index(header)
    i = src.index("{", start)
    depth, j = 0, i
    while j < len(src):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[i + 1:j]
        j += 1
    raise AssertionError(f"unbalanced block after {header!r}")


def _tokens(body: str) -> dict:
    return dict(re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", _strip_comments(body)))


_CLEAN = _strip_comments(CSS)
LIGHT = _tokens(_block(_CLEAN, ":root {"))
DARK_MEDIA = _tokens(_block(_block(_CLEAN, "@media (prefers-color-scheme: dark)"), ':root:not([data-theme="light"])'))
DARK_ATTR = _tokens(_block(_CLEAN, ':root[data-theme="dark"]'))


def _is_hex(v: str) -> bool:
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", v.strip()))


def _lum(hex_: str) -> float:
    h = hex_.strip().lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# (foreground, background, minimum ratio): 4.5 for text, 3 for graphics.
PAIRS = [
    ("text", "bg", 4.5), ("text", "surface", 4.5), ("text", "surface-2", 4.5),
    ("text-2", "surface", 4.5), ("muted", "bg", 4.5), ("muted", "surface", 4.5),
    ("muted", "surface-2", 4.5), ("accent", "bg", 4.5), ("accent", "surface", 4.5),
    ("accent", "surface-2", 4.5), ("accent", "accent-soft", 4.5),
    ("accent-ink", "accent-soft", 4.5), ("on-accent", "accent-fill", 4.5),
    ("gain", "surface", 4.5), ("gain", "surface-2", 4.5), ("gain", "gain-soft", 4.5),
    ("loss", "surface", 4.5), ("loss", "surface-2", 4.5), ("loss", "loss-soft", 4.5),
    ("warn", "surface", 4.5), ("warn", "warn-soft", 4.5),
    ("muted", "neutral-soft", 4.5), ("text-2", "neutral-soft", 4.5),
    ("gold", "surface", 4.5), ("gold", "gold-soft", 4.5),
    ("on-wa", "wa", 4.5),
    ("bar-buy", "surface", 3.0), ("bar-sell", "surface", 3.0),
    ("series-1", "surface", 3.0), ("series-2", "surface", 3.0),
]


def _theme(name: str) -> dict:
    return LIGHT if name == "light" else {**LIGHT, **DARK_ATTR}


def test_token_blocks_were_found():
    assert len(LIGHT) > 30 and len(DARK_ATTR) > 20


def test_both_dark_blocks_are_identical():
    """The @media block (follows the OS) and [data-theme=dark] (the toggle) must
    agree, or the toggle and the system setting would show different dark themes."""
    assert DARK_MEDIA == DARK_ATTR


def test_every_themed_colour_has_a_dark_counterpart():
    colour_tokens = {k for k, v in LIGHT.items() if _is_hex(v) or v.strip().startswith(("rgba(", "0 "))}
    missing = sorted(colour_tokens - set(DARK_ATTR) - THEME_INVARIANT)
    assert not missing, f"colour tokens defined for light only: {missing}"
    stray = sorted(set(DARK_ATTR) - set(LIGHT))
    assert not stray, f"dark-only tokens (no light definition on :root): {stray}"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg,minimum", PAIRS)
def test_contrast(theme, fg, bg, minimum):
    t = _theme(theme)
    ratio = contrast(t[fg], t[bg])
    assert ratio >= minimum, f"{theme}: --{fg} on --{bg} is {ratio:.2f}:1, needs {minimum}:1"


def test_contrast_check_can_fail():
    """Negative control: the check must reject a low-contrast pair."""
    assert contrast("#9aa0aa", "#ffffff") < 4.5
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)


def test_no_raw_colours_outside_the_token_blocks():
    body = _CLEAN
    for header in (":root {", '@media (prefers-color-scheme: dark)', ':root[data-theme="dark"]'):
        blk = _block(body, header)
        body = body.replace(blk, "", 1)
    raw = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)", body)
    # `#` followed by hex digits also matches id selectors like #add; require a
    # colour context to avoid that false positive.
    raw = [r for r in raw if not re.fullmatch(r"#[a-fA-F]{3}", r)]
    assert not raw, f"raw colours outside the :root token blocks: {raw[:10]}"


def test_no_inline_colours_in_markup_or_scripts():
    for name, src in (("index.html", HTML), ("app.js", JS)):
        styles = re.findall(r'style="([^"]*)"', re.sub(r"<symbol.*?</symbol>", "", src, flags=re.S))
        bad = [s for s in styles if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", s)]
        assert not bad, f"{name}: inline raw colours {bad[:3]}"


def test_fonts_are_self_hosted_and_present():
    assert "fonts.googleapis.com" not in HTML and "fonts.gstatic.com" not in HTML
    files = re.findall(r"url\(/fonts/([^)]+)\)", CSS)
    assert files, "no @font-face sources found"
    for f in files:
        assert (WEB / "fonts" / f).is_file(), f"missing font file {f}"
    assert (WEB / "fonts" / "OFL.txt").is_file(), "font licence must ship with the font"
    assert (WEB / "THIRD_PARTY_NOTICES.txt").is_file()


def test_every_icon_used_is_in_the_sprite():
    defined = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', HTML))
    used = set(re.findall(r'href="#i-([a-z0-9-]+)"', HTML))
    name = r"'([a-z0-9]+(?:-[a-z0-9]+)*)'"
    used |= set(re.findall(r"icon\(\s*" + name, JS))                                 # icon('x')
    for a, b in re.findall(r"icon\([^)?]*\?\s*" + name + r"\s*:\s*" + name, JS):  # icon(c ? 'a' : 'b')
        used |= {a, b}
    used |= set(re.findall(r"title\(\s*" + name, JS))                                # title('x', ...)
    used |= set(re.findall(r"card\(\s*'[a-z]+',\s*" + name, JS))                    # card('buy', 'x', ...)
    used |= set(re.findall(r"icon: ic = " + name, JS))                                # stateHtml default
    used |= set(re.findall(r"icon: " + name, JS))                                     # stateHtml({icon: 'x'})
    assert used, "found no icon references; the patterns above have drifted from app.js"
    missing = sorted(used - defined)
    assert not missing, f"icons referenced but not in the sprite: {missing}"


def test_asset_versions_match_and_theme_script_precedes_styles():
    css_v = re.search(r"/styles\.css\?v=(\d+)", HTML).group(1)
    js_v = re.search(r"/app\.js\?v=(\d+)", HTML).group(1)
    assert css_v == js_v
    # The saved theme must be applied before the stylesheet paints, or a dark-mode
    # user sees a light flash on every load.
    assert HTML.index("localStorage.getItem('theme')") < HTML.index("/styles.css")
