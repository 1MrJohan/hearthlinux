"""CLI stats: per-hero results and simulator calibration."""

from __future__ import annotations

from pathlib import Path

from bgtracker.data import cards
from bgtracker.history.db import DB_FILE, HistoryDB


def report(path: Path = DB_FILE) -> str:
    if not path.is_file():
        return "no match history yet"
    # Opened through HistoryDB, not a bare sqlite3.connect: the migrations live
    # there, and a read-only path that skips them reports on a stale schema.
    db = HistoryDB(path)
    conn = db.conn
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
    lines.append("== simulator calibration ==")
    ghosts = conn.execute(
        "SELECT COUNT(*) FROM combats WHERE opponent_is_ghost = 1"
    ).fetchone()[0]
    if ghosts:
        # Never drop rows silently: excluding them moves the top bucket by
        # more than ten points, and a reader deserves to know why.
        lines.append(
            f"  ({ghosts} ghost fight(s) excluded — a ghost's hero HP is not a"
            " readable signal, so a win against one cannot be told from a tie)"
        )
    lines.append("  predicted win% -> actual outcomes (ties are damage-free, not misses):")
    buckets = conn.execute(
        "SELECT MIN(CAST(predicted_win / 10 AS INT), 9) AS bucket, COUNT(*),"
        " SUM(outcome = 'win'), SUM(outcome = 'tie'), SUM(outcome = 'loss')"
        " FROM combats WHERE predicted_win IS NOT NULL AND outcome IS NOT NULL"
        " AND COALESCE(opponent_is_ghost, 0) = 0"
        " GROUP BY bucket ORDER BY bucket"
    ).fetchall()
    if not buckets:
        lines.append("    (no recorded combats with predictions)")
    for bucket, n, wins, ties, losses in buckets:
        low = min(bucket, 9) * 10
        lines.append(
            f"    {low:3d}-{low + 10:3d}%: {n:4d} combats  "
            f"win {100 * wins / n:3.0f}%  tie {100 * ties / n:3.0f}%  loss {100 * losses / n:3.0f}%"
        )
    # Loss calibration is the damage-relevant view: how often a predicted loss
    # actually costs you HP.
    lines.append("")
    lines.append("  predicted loss% -> actual loss% (damage-relevant):")
    loss_buckets = conn.execute(
        "SELECT MIN(CAST(predicted_loss / 10 AS INT), 9) AS bucket, COUNT(*),"
        " SUM(outcome = 'loss')"
        " FROM combats WHERE predicted_loss IS NOT NULL AND outcome IS NOT NULL"
        " AND COALESCE(opponent_is_ghost, 0) = 0"
        " GROUP BY bucket ORDER BY bucket"
    ).fetchall()
    for bucket, n, losses in loss_buckets:
        low = min(bucket, 9) * 10
        lines.append(f"    {low:3d}-{low + 10:3d}%: {n:4d} combats, actual loss {100 * losses / n:.0f}%")
    db.close()
    return "\n".join(lines)
