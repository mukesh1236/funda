#!/usr/bin/env python3
"""Compare OpenRouter models on the same questions — free vs paid — side by side.

Why: to decide whether a paid model is worth it over the free tier, run the
SAME prompts through several models and eyeball answer quality, latency, and
token usage.

Usage:
    export OPENROUTER_API_KEY=sk-or-v1-...        # your key (never commit it)
    python scripts/compare_models.py

    # optional overrides:
    python scripts/compare_models.py --models "deepseek/deepseek-chat-v3.1:free,openai/gpt-4o-mini"
    python scripts/compare_models.py --q "Is NVDA a strong buy and why?"

Notes:
  - ':free' models cost $0. Paid models are billed per token from your balance.
  - This calls OpenRouter directly (not the app), so answers won't include the
    app's live analyst-data grounding — it's a raw model-quality/latency
    comparison, which is what you want for choosing a model.
"""
import argparse
import os
import sys
import time

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = [
    "deepseek/deepseek-chat-v3.1:free",   # what the app uses now — $0
    "openai/gpt-4o-mini",                 # cheap, strong paid baseline
    # add more to taste, e.g. "anthropic/claude-3.5-sonnet"
]

DEFAULT_QUESTIONS = [
    "In one paragraph, explain why a stock's analyst price target might be far above its current price.",
    "What does a high analyst 'conviction' score tell an investor, and what are its limits?",
    "Summarize the trade-offs between buying an index ETF vs picking individual stocks.",
]


def ask(key: str, model: str, question: str, timeout: float = 60) -> dict:
    started = time.perf_counter()
    try:
        r = httpx.post(
            URL,
            headers={"Authorization": f"Bearer {key}",
                     "X-Title": "AlphaFunds model comparison"},
            json={"model": model,
                  "messages": [{"role": "user", "content": question}],
                  "max_tokens": 500},
            timeout=timeout,
        )
        dur = time.perf_counter() - started
        if r.status_code != 200:
            return {"ok": False, "err": f"HTTP {r.status_code}: {r.text[:200]}", "dur": dur}
        data = r.json()
        text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        usage = data.get("usage", {})
        return {"ok": True, "text": text, "dur": dur,
                "in_tok": usage.get("prompt_tokens"), "out_tok": usage.get("completion_tokens")}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}", "dur": time.perf_counter() - started}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--models", help="comma-separated model ids", default=",".join(DEFAULT_MODELS))
    p.add_argument("--q", help="single question (repeatable use: run again)", action="append")
    args = p.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("ERROR: set OPENROUTER_API_KEY in your environment first.", file=sys.stderr)
        return 1

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    questions = args.q or DEFAULT_QUESTIONS

    for qi, q in enumerate(questions, 1):
        print("\n" + "=" * 78)
        print(f"Q{qi}: {q}")
        print("=" * 78)
        for model in models:
            free = ":free" in model
            res = ask(key, model, q)
            tag = "FREE" if free else "PAID"
            print(f"\n--- [{tag}] {model} ---")
            if not res["ok"]:
                print(f"  (failed: {res['err']}  [{res['dur']:.1f}s])")
                continue
            meta = f"{res['dur']:.1f}s"
            if res.get("in_tok") is not None:
                meta += f" · {res['in_tok']} in / {res['out_tok']} out tokens"
                if free:
                    meta += " · cost $0"
            print(f"  ({meta})")
            print("  " + res["text"].replace("\n", "\n  "))
    print("\nDone. ':free' rows cost nothing; paid rows drew from your balance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
