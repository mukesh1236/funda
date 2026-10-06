"""The fund compare tables must size to the card on a phone.

Regression (6 Oct 2026): the global `th, td { white-space: nowrap }` let a long fund
name make the compare table 580px wide on a 390px phone, pushing the second fund's
column off-screen. Measured in Chromium; pinned here as the rules that prevent it.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
CSS = (WEB / "styles.css").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")


def test_compare_tables_use_fixed_layout_and_wrapping_cells():
    assert re.search(r"\.cmp-table,\s*\.cmp-shared\s*\{\s*table-layout:\s*fixed", CSS)
    cells = re.search(r"\.cmp-table td[^{]*\{([^}]*)\}", CSS, re.S).group(1)
    assert "white-space: normal" in cells and "overflow-wrap" in cells


def test_compare_markup_uses_the_classes_and_puts_the_name_under_the_ticker():
    assert 'class="mini cmp-shared"' in JS
    assert 'class="cmp-fund-name"' in JS
    assert "${esc(fa.symbol)} · ${esc(fa.name)}" not in JS   # the long one-line header
