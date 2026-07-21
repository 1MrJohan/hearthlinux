"""CLI stats: per-hero results and simulator calibration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bgtracker.data import cards
from bgtracker.history.db import DB_FILE


def report(path: Path = DB_FILE) -> str:
    if not path.is_file():
        return "no match history yet"
    conn = sqlite3.connect(path)
    lines: list[str] = []

    rows = conn.execute(
        "SELECT hero_card_id, COUNT(*), AVG(placement),"
        " SUM(CASE WHEN placement <= 4 THEN 1 ELSE 0 END)"
        " FROM games WHERE placement IS NOT NULL"
        " GROUP BY hero_card_id ORDER BY COUNT(*) DESC"
    ).fetchall()
    lines.append("== heroes ==")
    if not rows:
        lines.append("  (no finished games)")
    for hero, games, avg_place, top4 in rows:
        lines.append(
            f"  {cards.name(hero):30s} {games:3d} games"
            f"  avg place {avg_place:.2f}  top4 {100 * top4 / games:.0f}%"
        )

    ratings = conn.execute(
        "SELECT recorded_at, rating FROM ratings ORDER BY recorded_at"
    ).fetchall()
    if ratings:
        lines.append("")
        lines.append("== MMR (recorded via `bgtracker mmr <value>`) ==")
        prev = None
        for at, rating in ratings[-10:]:
            delta = f" ({rating - prev:+d})" if prev is not None else ""
            lines.append(f"  {at[:16]}  {rating}{delta}")
            prev = rating

    lines.append("")
    lines.append("== simulator calibration (predicted win% vs actual) ==")
    buckets = conn.execute(
        "SELECT CAST(predicted_win / 10 AS INT) AS bucket,"
        " COUNT(*), SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END)"
        " FROM combats WHERE predicted_win IS NOT NULL AND outcome IS NOT NULL"
        " GROUP BY bucket ORDER BY bucket"
    ).fetchall()
    if not buckets:
        lines.append("  (no recorded combats with predictions)")
    for bucket, n, wins in buckets:
        low = min(bucket, 9) * 10
        lines.append(f"  predicted {low:3d}-{low + 10:3d}%: {n:4d} combats, actual win {100 * wins / n:.0f}%")
    conn.close()
    return "\n".join(lines)
