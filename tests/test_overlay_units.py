"""Pure logic behind the overlay's rendering — no widgets, no display."""

from __future__ import annotations

import pytest

from bgtracker.state.game import Enchantment, Minion

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from bgtracker.overlay.hud import bar_widths  # noqa: E402
from bgtracker.overlay.rail import status_labels  # noqa: E402
from bgtracker.overlay.widgets import magnet_pip, pip_classes  # noqa: E402
from bgtracker.parse.events import Standing  # noqa: E402


# -- odds bar ----------------------------------------------------------
@pytest.mark.parametrize(
    "odds",
    [(63, 9, 28), (100, 0, 0), (0, 0, 100), (33, 33, 34), (1, 98, 1), (50, 0, 50)],
)
def test_bar_segments_always_fill_the_track(odds):
    track = 274
    widths = bar_widths(*odds, track)
    assert sum(widths) == track, "a rounding gap would leave a notch in the bar"
    assert all(w >= 0 for w in widths)


def test_bar_widths_are_proportional():
    win, tie, loss = bar_widths(50, 25, 25, 200)
    assert (win, tie, loss) == (100, 50, 50)


def test_bar_widths_hide_zero_shares():
    win, tie, loss = bar_widths(100, 0, 0, 274)
    assert (win, tie, loss) == (274, 0, 0)


@pytest.mark.parametrize("track", [0, -5])
def test_bar_widths_tolerate_an_unrealised_track(track):
    # Before the panel is measured the track can be 0; must not divide by it.
    assert bar_widths(63, 9, 28, track) == (0, 0, 0)


def test_bar_widths_tolerate_absent_odds():
    assert bar_widths(0, 0, 0, 274) == (0, 0, 0)


# -- keyword pips ------------------------------------------------------
def _minion(**flags) -> Minion:
    return Minion(entity_id=1, card_id="X", position=1, attack=1, health=1, **flags)


def test_pips_follow_flag_order():
    minion = _minion(taunt=True, divine_shield=True, reborn=True)
    assert pip_classes(minion.flags) == ["pip-t", "pip-d", "pip-r"]


def test_golden_is_not_a_pip():
    # Golden is the tile's border treatment; a pip would double up on it.
    minion = _minion(golden=True, taunt=True)
    assert "G" in minion.flags
    assert pip_classes(minion.flags) == ["pip-t"]


def test_plain_minion_has_no_pips():
    assert pip_classes(_minion().flags) == []


def test_unknown_letters_are_ignored():
    assert pip_classes("TQZD") == ["pip-t", "pip-d"]


def test_windfury_variants_collapse_to_one_pip():
    assert pip_classes(_minion(windfury=True, mega_windfury=True).flags) == ["pip-w"]


# -- leaderboard status -----------------------------------------------
def test_rail_status_shows_numeric_health_and_tier_for_the_player():
    standing = Standing(
        place=3, player_id=1, hero_card_id="HERO", health=18,
        tier=3, you=True,
    )
    assert status_labels(standing) == ("18", "T3")


@pytest.mark.parametrize("tier", [None, 0, 7])
def test_rail_status_labels_missing_or_invalid_tier_as_unknown(tier):
    standing = Standing(
        place=1, player_id=2, hero_card_id="HERO", health=25, armor=5,
        tier=tier,
    )
    assert status_labels(standing) == ("30", "T—")


# -- magnetized-cards pip ------------------------------------------------
def _magnetic(entity_id: int, card_id: str) -> Enchantment:
    return Enchantment(card_id=card_id, num1=4, num2=4, magnetic=True)


def test_plain_minion_has_no_magnet_pip():
    assert magnet_pip(_minion()) is None


def test_one_linked_card_is_a_bare_m():
    minion = _minion(enchantments=(_magnetic(1, "BG31_171te"),))
    assert magnet_pip(minion) == "M"


def test_linked_cards_are_counted_on_the_pip():
    # Two *distinct* cards. Repeats of one card fold into a single
    # enchantment upstream, so this can never overstate the count.
    minion = _minion(enchantments=(_magnetic(1, "BG31_171te"), _magnetic(2, "BG_BOT_911e")))
    assert magnet_pip(minion) == "M2"


def test_ordinary_buffs_do_not_earn_a_magnet_pip():
    buff = Enchantment(card_id="BG26_146e2", num1=9, num2=9)
    assert magnet_pip(_minion(enchantments=(buff,))) is None


def test_magnet_pip_has_a_colour():
    # pip_classes ignores letters the palette lacks; "M" is not a flag letter
    # and reaches the tile through magnet_pip instead, so it needs its own.
    from bgtracker.overlay import theme
    assert "M" in theme.PIP_COLOURS
