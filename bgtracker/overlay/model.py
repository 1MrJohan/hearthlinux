"""What the overlay is currently showing, as plain data.

Until this existed, everything the overlay knew lived in its widgets: the phase
title was whatever string was last pushed into a `Gtk.Label`. That is fine while
the window lives forever, and fatal the moment it doesn't — changing
`overlay_scale` has to destroy and rebuild the window, and a rebuilt window
would have come up blank until the next event happened to arrive, mid-game.

So the app keeps an `OverlayState`, and `render` is the only thing that touches
widgets. A rebuild is then just `render(new_window, state)` with no `previous`.

**`render` diffs on purpose.** It emits only the calls whose value actually
changed, for two reasons. Cheap one: `OverlayWindow._set_content` re-clamps the
panel and re-uploads the input region, so re-pushing an unchanged board on every
event would be real churn several times a second. Load-bearing one: *not*
calling a setter is itself behaviour — the combat result must stay banked until
`ShopReady`, not revealed the moment it lands mid-fight. A renderer that pushed
everything every time would quietly undo that.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class BoardView:
    """A board panel's contents. `None` in place of one means "cleared"."""

    title: str
    subtitle: str = ""
    board: object = None
    hero_card_id: str | None = None
    dead: bool = False


@dataclass
class OverlayState:
    """Every value the overlay displays.

    Defaults must match what a freshly built `OverlayWindow` already shows, so
    that diffing against a fresh `OverlayState()` correctly emits nothing.
    """

    phase: tuple[str, str] = ("", "")
    status: str = ""
    turn: int | None = None
    combat: bool = False
    # (win, tie, loss); all None means "no forecast".
    odds: tuple[float | None, float | None, float | None] = (None, None, None)
    damage: tuple[str | None, str | None] = (None, None)
    lethal: float | None = None
    # (outcome, damage) of the fight just forecast.
    result: tuple[str | None, int] = (None, 0)
    # HudPanel comes up expanded; only ShopReady collapses it.
    forecast_live: bool = True
    board: BoardView | None = None
    next_board: BoardView | None = None
    next_forecast: str | None = None
    hover_board: BoardView | None = None
    standings: tuple = ()
    buffs: tuple[tuple, tuple] = ((), ())
    hot_place: int | None = None

    def snapshot(self) -> OverlayState:
        """A shallow copy, for recording what the window was last told."""
        return replace(self)


def _render_board(setter, clearer, view: BoardView | None, *, orb: bool = False) -> None:
    if view is None:
        clearer()
    elif orb:
        setter(view.title, view.subtitle, view.board, view.hero_card_id, view.dead)
    else:
        setter(view.title, view.subtitle, view.board)


def render(window, state: OverlayState, previous: OverlayState | None = None) -> None:
    """Push `state` into `window`, emitting only what changed since `previous`.

    `previous=None` means "this window shows nothing yet" and emits the whole
    state — the rebuild path.
    """

    def changed(name: str) -> bool:
        return previous is None or getattr(previous, name) != getattr(state, name)

    if changed("phase"):
        window.set_phase(*state.phase)
    if changed("status"):
        window.set_status(state.status)
    if changed("turn"):
        window.set_turn(state.turn)
    if changed("combat"):
        window.set_combat(state.combat)
    # Before damage and lethal: clearing the odds clears both inside the
    # window, so the reverse order would wipe values set moments earlier.
    if changed("odds"):
        window.set_odds(*state.odds)
    if changed("damage"):
        window.set_damage(*state.damage)
    if changed("lethal"):
        window.set_lethal(state.lethal)
    if changed("result"):
        window.set_result(*state.result)
    if changed("forecast_live"):
        window.set_forecast_live(state.forecast_live)
    if changed("board"):
        _render_board(window.set_board, window.clear_board, state.board)
    if changed("next_board"):
        _render_board(window.set_next_board, window.clear_next_board, state.next_board)
    if changed("next_forecast"):
        window.set_next_forecast(state.next_forecast)
    if changed("hover_board"):
        _render_board(
            window.set_hover_board, window.clear_hover_board, state.hover_board, orb=True
        )
    if changed("standings"):
        window.set_standings(state.standings)
    if changed("buffs"):
        window.set_buffs(*state.buffs)
    if changed("hot_place"):
        window.set_hot_place(state.hot_place)


__all__ = ["BoardView", "OverlayState", "render"]
