"""Default notifier: print the digest. Zero setup; proves the pipeline."""
import logging

from app.notifications.base import Notifier

logger = logging.getLogger(__name__)


class ConsoleNotifier(Notifier):
    def send(self, digest: dict) -> None:
        lines = [
            "",
            "=" * 60,
            f"  Analyst Recommendation Digest — {digest.get('date', '')}",
            "=" * 60,
            f"  New recommendations: {digest.get('new_recommendations', 0)}",
            f"  Targets hit today:   {digest.get('targets_hit', 0)}",
            "",
            "  Top consensus stocks:",
        ]
        for s in digest.get("top_stocks", []):
            lines.append(
                f"    {s['symbol']:<6} "
                f"buy={s['buy_count']:<3} hold={s['hold_count']:<3} "
                f"sell={s['sell_count']:<3} score={s['consensus_score']:+d}"
                + (f"  target~{s['avg_target']}" if s.get("avg_target") else "")
            )
        lines.append("=" * 60)
        print("\n".join(lines))
        logger.info("Digest delivered to console (%s stocks)",
                    len(digest.get("top_stocks", [])))
