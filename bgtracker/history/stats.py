"""CLI stats: per-hero results and simulator calibration."""

from __future__ import annotations

from pathlib import Path

from bgtracker.data import cards
from bgtracker.history.db import DB_FILE, HistoryDB

# Every non-ghost combat with what calibration can honestly say about it.
# A row with no recorded outcome is not simply unknown: the classifier has
# always returned 'loss' first whenever your HP dropped and your board was
# readable, so a missing outcome with a readable start board means your HP
# did not drop — unless this was the fight that killed you, when there is no
# end board to read at all. So it is a proven non-loss when you are known to
# have survived it: a later fight in the same game, or a won game. Most such
# rows are lethal wins recorded before the 2026-09-22 elimination fix.
_SCORED = (
    "WITH scored AS ("
    " SELECT c.predicted_win, c.predicted_loss,"
    "  CASE WHEN c.outcome IS NOT NULL THEN c.outcome"
    "   WHEN c.my_board IS NOT NULL AND (g.placement = 1 OR c.turn <"
    "    (SELECT MAX(later.turn) FROM combats later WHERE later.game_id = c.game_id))"
    "   THEN 'not_loss' END AS result"
    " FROM combats c JOIN games g ON g.id = c.game_id"
    " WHERE COALESCE(c.opponent_is_ghost, 0) = 0) "
)


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
    not_loss, top_not_loss, unknown = conn.execute(
        _SCORED + "SELECT SUM(result = 'not_loss'),"
        " SUM(result = 'not_loss' AND predicted_win >= 90), SUM(result IS NULL)"
        " FROM scored WHERE predicted_win IS NOT NULL"
    ).fetchone()
    if not_loss:
        # Disclosed for the same reason as the ghosts: these concentrate in the
        # top bucket, so leaving them out silently understates it.
        lines.append(
            f"  ({not_loss} fight(s) have no recorded outcome but did not cost HP,"
            f" {top_not_loss} of them predicted 90-100%: a win cannot be told from a"
            " tie, so they are left out of the win table and scored as non-losses"
            " below)"
        )
    if unknown:
        lines.append(
            f"  ({unknown} fight(s) excluded with no outcome at all — mostly the last"
            " fight of a lost game, which may be the one that killed you)"
        )
    lines.append("  predicted win% -> actual outcomes (ties are damage-free, not misses):")
    buckets = conn.execute(
        _SCORED + "SELECT MIN(CAST(predicted_win / 10 AS INT), 9) AS bucket, COUNT(*),"
        " SUM(result = 'win'), SUM(result = 'tie'), SUM(result = 'loss')"
        " FROM scored WHERE predicted_win IS NOT NULL"
        " AND result IN ('win', 'tie', 'loss')"
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
        _SCORED + "SELECT MIN(CAST(predicted_loss / 10 AS INT), 9) AS bucket, COUNT(*),"
        " SUM(result = 'loss')"
        " FROM scored WHERE predicted_loss IS NOT NULL AND result IS NOT NULL"
        " GROUP BY bucket ORDER BY bucket"
    ).fetchall()
    for bucket, n, losses in loss_buckets:
        low = min(bucket, 9) * 10
        lines.append(f"    {low:3d}-{low + 10:3d}%: {n:4d} combats, actual loss {100 * losses / n:.0f}%")
    db.close()
    return "\n".join(lines)
