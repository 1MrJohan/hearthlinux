"""Typed events emitted by the live parser onto the app-wide queue."""

from __future__ import annotations

from dataclasses import dataclass

from bgtracker.state.game import BoardSnapshot, PlayerBoard


@dataclass(frozen=True)
class GameStart:
    game_type: int | None = None
    # Log-derived identity (game start timestamp) — stable across tracker
    # restarts that replay the same log, used to dedupe history rows.
    log_id: str | None = None


@dataclass(frozen=True)
class HeroPicked:
    card_id: str


@dataclass(frozen=True)
class TurnChange:
    turn: int


@dataclass(frozen=True)
class CombatStart:
    snapshot: BoardSnapshot


@dataclass(frozen=True)
class CombatEnd:
    snapshot: BoardSnapshot


@dataclass(frozen=True)
class CombatForecast:
    """A provisional forecast for a fight already in progress.

    Raised repeatedly as a run tightens, before the `CombatStart` that carries
    the final numbers. Purely a display refresh — nothing durable should be
    derived from it, because the figures are still moving.
    """

    snapshot: BoardSnapshot


@dataclass(frozen=True)
class CombatResult:
    """How the fight the tracker just forecast actually went.

    Derived by the pipeline from the HP swing across the combat, not parsed —
    it exists so the overlay can show the forecast against what happened
    instead of the forecast alone. Raised alongside `CombatEnd`, which means it
    arrives while the player is still watching the battle animate: whoever
    displays it must hold it until `ShopReady`.
    """

    turn: int
    outcome: str | None      # 'win' | 'tie' | 'loss' | 'ghost' | None (unknown)
    damage: int = 0          # HP that actually changed hands


@dataclass(frozen=True)
class ShopReady:
    """The combat animation finished and the player is back at the shop.

    CombatEnd fires when the engine has *resolved* the fight, which happens
    about a second after it starts; the client then spends 20-45s animating
    it. Anything the player looks at — the phase title, the odds — must follow
    this event instead, or it changes while they are still watching the fight.
    """


@dataclass(frozen=True)
class ShopBoard:
    """The friendly board changed during the recruit phase.

    Emitted only when the projection actually differs from the last one, so a
    consumer can treat every one of these as a real change.
    """

    board: PlayerBoard
    turn: int


@dataclass(frozen=True)
class ShopForecast:
    """Odds against the next opponent's last-seen board, from the shop.

    A guess, and labelled as one: the opponent goes on buying after you last
    saw them, so `seen_turn` is how stale the board behind this number is.
    """

    opponent_id: int
    seen_turn: int
    turn: int


@dataclass(frozen=True)
class NextOpponent:
    player_id: int


@dataclass(frozen=True)
class Standing:
    """One player's row on the leaderboard."""

    place: int
    player_id: int          # hero PLAYER_ID tag — the stable per-player identity
    hero_card_id: str | None
    health: int = 0
    armor: int = 0
    dead: bool = False
    you: bool = False

    @property
    def total_health(self) -> int:
        return self.health + self.armor


@dataclass(frozen=True)
class Standings:
    """Current leaderboard order, best place first."""

    places: tuple[Standing, ...]


@dataclass(frozen=True)
class GameEnd:
    placement: int | None


@dataclass(frozen=True)
class Buffs:
    """Friendly player's tavern buffs, held tavern spells/trinkets, and turn economy.

    entries: (label, atk, health) accumulating counters, applied when a minion
             is played and so already baked into the board.
    spells:  card ids of persistent tavern spells/trinkets held.
    shop:    (label, atk, health) buffs a minion already carries while it sits
             in Bob's tavern — a buying decision, invisible to combat.
    gold_next_turn: net gold banked for next turn; negative if overdrawn.
    free_rerolls:   free rerolls available this turn.
    """

    entries: tuple[tuple[str, int, int], ...]
    spells: tuple[str, ...] = ()
    shop: tuple[tuple[str, int, int], ...] = ()
    gold_next_turn: int = 0
    free_rerolls: int = 0


Event = (
    GameStart
    | HeroPicked
    | TurnChange
    | CombatStart
    | CombatForecast
    | CombatEnd
    | CombatResult
    | ShopReady
    | ShopBoard
    | ShopForecast
    | NextOpponent
    | Standings
    | GameEnd
    | Buffs
)
