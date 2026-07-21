"""Console renderer for BG events — Phase 1 output and permanent debug mode."""

from __future__ import annotations

from bgtracker.data import cards
from bgtracker.parse import events as ev
from bgtracker.state.game import BoardSnapshot, Minion, PlayerBoard


def render_minion(m: Minion) -> str:
    kw = m.flags
    return f"{cards.name(m.card_id)} {m.attack}/{m.health}{' [' + kw + ']' if kw else ''}"


def render_board_line(board: PlayerBoard) -> str:
    return ", ".join(render_minion(m) for m in board.minions) or "(empty board)"


def _board_lines(label: str, board: PlayerBoard | None) -> list[str]:
    if board is None:
        return [f"  {label}: <unknown>"]
    head = (
        f"  {label}: {cards.name(board.hero_card_id)} "
        f"HP {board.health}+{board.armor} armor, tier {board.tier}"
    )
    return [head] + [f"    {i + 1}. {render_minion(m)}" for i, m in enumerate(board.minions)]


def render_snapshot(snap: BoardSnapshot) -> str:
    return "\n".join(
        _board_lines("you", snap.friendly) + _board_lines("opp", snap.opponent)
    )


def print_event(event: ev.Event) -> None:
    match event:
        case ev.GameStart(game_type=gt):
            print(f"=== game start (type {gt}) ===")
        case ev.HeroPicked(card_id=cid):
            print(f"hero: {cards.name(cid)}")
        case ev.TurnChange(turn=t):
            print(f"-- turn {t} --")
        case ev.CombatStart(snapshot=s):
            print(f"COMBAT (turn {s.turn}):")
            print(render_snapshot(s))
        case ev.CombatEnd(snapshot=s):
            you = s.friendly
            hp = f"{you.health}+{you.armor}" if you else "?"
            print(f"combat over — your HP {hp}")
        case ev.NextOpponent(player_id=pid):
            print(f"next opponent: player {pid}")
        case ev.GameEnd(placement=p):
            print(f"=== game end — placement: {p if p else '?'} ===")
