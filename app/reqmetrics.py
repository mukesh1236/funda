"""Real request telemetry for the SRE dashboard.

The timing middleware records every /api request here (in-memory ring buffer
— zero DB writes on the hot path; this app runs single-process on Railway).
summary() turns the buffer into the dashboard's numbers: error rate, latency
percentiles, hourly traffic, slowest endpoints, process uptime.

Honesty note: an app cannot observe its own downtime, so we report what we
CAN measure truthfully — process uptime, in-request error rate, and latency —
not a fabricated availability number. External uptime belongs to an external
monitor (TeamOps' http_health connector does exactly that).
"""
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

_MAX_SAMPLES = 20000          # ~24h of traffic for a small app; bounded memory
_WINDOW_SECONDS = 24 * 3600

# (epoch_ts, path_group, status, dur_ms)
_samples: Deque[Tuple[float, str, int, float]] = deque(maxlen=_MAX_SAMPLES)
_lock = threading.Lock()
_process_start = time.time()

_ID_SEGMENT = re.compile(r"/[A-Z0-9.\-]{1,12}$")


def _group(path: str) -> str:
    """Collapse per-symbol paths so /api/recommendations/NVDA and .../AAPL
    aggregate as one endpoint."""
    p = path.split("?")[0]
    return _ID_SEGMENT.sub("/{sym}", p)


def record(path: str, status: int, dur_ms: float) -> None:
    if not path.startswith("/api"):
        return
    with _lock:
        _samples.append((time.time(), _group(path), status, dur_ms))


def _percentile(sorted_vals: List[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def process_uptime_seconds() -> int:
    return int(time.time() - _process_start)


def summary() -> Dict:
    cutoff = time.time() - _WINDOW_SECONDS
    with _lock:
        window = [s for s in _samples if s[0] >= cutoff]

    total = len(window)
    err5 = sum(1 for s in window if s[2] >= 500)
    err4 = sum(1 for s in window if 400 <= s[2] < 500)
    durs = sorted(s[3] for s in window)

    # Hourly buckets for the traffic/error heatmap (UTC hour of day).
    hourly_reqs = [0] * 24
    hourly_errs = [0] * 24
    for ts, _, status, _dur in window:
        h = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        hourly_reqs[h] += 1
        if status >= 500:
            hourly_errs[h] += 1

    # Per-endpoint aggregates → slowest endpoints (by p95), min 3 samples.
    by_ep: Dict[str, List[float]] = {}
    for _ts, ep, _status, dur in window:
        by_ep.setdefault(ep, []).append(dur)
    slowest = sorted(
        (
            {"endpoint": ep, "count": len(v),
             "p95_ms": round(_percentile(sorted(v), 0.95), 1)}
            for ep, v in by_ep.items() if len(v) >= 3
        ),
        key=lambda e: e["p95_ms"], reverse=True,
    )[:5]

    # p95 latency trend: 12 two-hour buckets across the window.
    bucket_durs: Dict[int, List[float]] = {}
    for ts, _ep, _status, dur in window:
        bucket_durs.setdefault(int((ts - cutoff) // 7200), []).append(dur)
    p95_series = [
        round(_percentile(sorted(bucket_durs[b]), 0.95), 1)
        for b in sorted(bucket_durs)
    ]

    return {
        "window_hours": 24,
        "requests": total,
        "error_rate_5xx": round(err5 / total, 4) if total else None,
        "error_rate_4xx": round(err4 / total, 4) if total else None,
        "p50_ms": round(_percentile(durs, 0.50), 1) if durs else None,
        "p95_ms": round(_percentile(durs, 0.95), 1) if durs else None,
        "p99_ms": round(_percentile(durs, 0.99), 1) if durs else None,
        "hourly_requests": hourly_reqs,
        "hourly_errors": hourly_errs,
        "slowest_endpoints": slowest,
        "p95_series": p95_series,
        "process_uptime_seconds": process_uptime_seconds(),
    }


def reset() -> None:
    """Test helper."""
    with _lock:
        _samples.clear()
