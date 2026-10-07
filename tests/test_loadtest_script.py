"""scripts/loadtest.py must refuse to hit a real host by accident, and judge steps correctly."""
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("loadtest", ROOT / "scripts" / "loadtest.py")
lt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lt)

ARGS = SimpleNamespace(max_error_pct=1.0, p95_ms=1500)


def test_remote_target_is_refused_without_the_flag():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/loadtest.py"),
                        "--base-url", "https://example.up.railway.app", "--steps", "1"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "allow-remote" in (r.stdout + r.stderr)


def test_percentiles():
    assert lt.pct([], 95) == 0.0
    assert lt.pct(list(range(1, 101)), 50) in (50, 51)
    assert lt.pct(list(range(1, 101)), 95) in (95, 96)


def test_business_answers_are_not_failures_but_server_trouble_is():
    assert lt.ok(200) and lt.ok(404) and lt.ok(422)
    assert not lt.ok(500) and not lt.ok(502) and not lt.ok(429) and not lt.ok(401)
    assert not lt.ok("ERR:ReadTimeout")


def _rec(samples):
    r = lt.Recorder()
    r.samples = samples
    return r


def test_a_step_passes_only_when_latency_and_errors_are_inside_the_limits():
    fast = [("feed", 200, 50.0)] * 99 + [("login", 200, 300.0)]
    assert lt.summarise(_rec(fast), 10, 10, ARGS)["passed"]
    slow = [("feed", 200, 4000.0)] * 100
    assert not lt.summarise(_rec(slow), 10, 10, ARGS)["passed"]
    flaky = [("feed", 200, 50.0)] * 90 + [("feed", 500, 50.0)] * 10
    assert not lt.summarise(_rec(flaky), 10, 10, ARGS)["passed"]
    assert not lt.summarise(_rec([]), 10, 10, ARGS)["passed"]
