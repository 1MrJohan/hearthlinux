# Tavern shop buffs in the Buffs panel

Date: 2026-07-24

## Problem

Playing an Elemental board with Nomi, Kitchen Nightmare, the Buffs panel showed
nothing about elementals. The panel reads four accumulating player counters
(`_BUFF_TAGS` in `state/game.py`): Blood Gem, Elemental, Pirate, Tavern Spell.
Nomi's buff is not among them, and neither is any other effect that buffs
minions **while they sit in Bob's tavern**.

Those effects are in the log, under a card-id family nobody had looked for:

```
SHOW_ENTITY - Updating Entity=5847 CardID=BG_ShopBuff_Elemental
  tag=CONTROLLER value=8      tag=CARDTYPE value=ENCHANTMENT
  tag=ATTACHED value=23       tag=ZONE value=PLAY
TAG_CHANGE Entity=5847 tag=TAG_SCRIPT_DATA_NUM_1 value=27   ← +27 attack
TAG_CHANGE Entity=5847 tag=TAG_SCRIPT_DATA_NUM_2 value=27   ← +27 health
```

Entity 23 is the friendly Player entity. One enchantment exists per tribe, per
player, holding a running total that several cards feed at once — in the
observed game Dune Dweller, Nomi, and a Nomi Sticker magic item all stacked into
the same +27/+27. Six captured sessions all contain at least the generic
`BG_ShopBuff`, so this is common, not a corner case.

## Scope

This is a **display** change. It adds a second, labelled group to the existing
Buffs panel and touches nothing else.

The two kinds of number in the panel answer different questions, which is why
they get separate groups rather than one flat list:

- **Existing rows** — what a minion gets when you *play* it. Already baked into
  the board and already sent to the simulator as `globalInfo`.
- **New rows** — what a minion in the tavern is *already carrying* before you
  buy it. A purchasing decision, invisible to combat.

```
┌─ Tavern Buffs ──────────┐
│  ● Blood Gem     +2/+2  │
│  ● Spell         +1/+1  │
│                         │
│  In Bob's Tavern        │
│  ● Elemental   +27/+27  │
│  ● All minions  +1/+1   │
└─────────────────────────┘
```

### Not in scope

- **Nothing goes to the simulator.** A shop buff is already in a minion's live
  `ATK`/`HEALTH` by the time you own it; sending it would double-count.
  `_global_info` and `sim/mapper.py` are untouched.
- **The unmapped `globalInfo` counters** found while investigating
  (`ElementalsPlayedThisTurn`, `TavernSpellsCastThisGame`, `GoldSpentThisGame`
  and ~18 others the pinned simulator reads but the mapper never sends). That is
  a real odds gap and a separate change, judged by `resim` Brier rather than by
  a panel.
- **Opponent shop buffs**, matching the panel's existing friendly-only scope.
- The existing `BACON_ELEMENTAL_BUFFATKVALUE` row stays. It is a different
  quantity from the shop buff and it does still fire — two of six captured
  sessions have it.

## Design

### 1. Reader — `state/game.py`

A sibling to `read_buffs`, keyed by an exact card-id set:

```python
_SHOP_BUFFS = {
    "BG_ShopBuff": "All minions",
    "BG_ShopBuff_MultiRace": "Multi-tribe",
    "BG_ShopBuff_Beast": "Beast",
    ...  # the ten tribes
}

def read_shop_buffs(game: Game, player_id: int) -> tuple[tuple[str, int, int], ...]
```

Filter: `card_id in _SHOP_BUFFS` and `CONTROLLER == player_id` and
`ZONE == Zone.PLAY`. Value from `TAG_SCRIPT_DATA_NUM_1` (attack) and
`TAG_SCRIPT_DATA_NUM_2` (health); rows where both are zero are dropped. Returns
the same `(label, atk, hp)` shape the panel already renders, in the dict's
declared order so rows keep their place as values change.

Two things the comment must carry, because both are silent if got wrong:

- **Exact ids, not a prefix.** The sibling `BG_ShopBuff*_Ench` ids are the
  per-minion "Tavern Buffed" marks attached to the shop minions themselves. A
  prefix match would sum those into the player total.
- **The value is in `TAG_SCRIPT_DATA_NUM_1/2`**, not `ATK`/`HEALTH`, which on
  these entities are absent.

`BG_ShopBuff_MultiRace` covers several tribes but the log does not say which, so
it is labelled "Multi-tribe" rather than guessing.

### 2. Plumbing — the existing spine, unchanged

- `ev.Buffs` gains `shop: tuple[tuple[str, int, int], ...] = ()`.
- `exporter.py::maybe_emit_buffs` folds `shop` into the existing change
  comparison, so it re-emits on the same trigger and at the same rate as today.
- `OverlayState.buffs` becomes a 3-tuple; `app.py`'s `case ev.Buffs` and the
  `st.buffs = ((), ())` game-reset both follow.

No new event, no new listener, no new panel.

### 3. Rendering — `overlay/window.py`, `overlay/theme.py`

`set_buffs(entries, spells, shop)` appends a `.buff-group` subheading
("In Bob's Tavern") followed by shop rows built with the existing `_buff_chip`.
The subheading and the rows are emitted only when `shop` is non-empty. The
`_set_content("buffs", ...)` gate becomes "any of the three lists is non-empty".

`BUFF_COLOURS` needs ten new keys. The design handoff defines no tribe palette
(its only tribe-adjacent colours are the keyword pips), so these extend the four
existing buff dots in the same muted jewel-tone register rather than starting a
second palette:

| Label | Colour | Label | Colour |
|---|---|---|---|
| Beast | `#8fae54` | Naga | `#6f7fd0` |
| Demon | `#b45fa8` | Quilboar | `#c96f86` |
| Dragon | `#d1743f` | Undead | `#b9b3a0` |
| Mech | `#7f96b0` | All minions | `#e0cfa4` |
| Murloc | `#4fbf9a` | Multi-tribe | `#a9b4c0` |

Elemental and Pirate already have dots and keep them.

### 4. Testing

- `read_shop_buffs` against synthetic entities: the happy path; `_Ench` siblings
  ignored; another controller's enchantment ignored; a zero-valued enchantment
  suppressed.
- A new test in `test_overlay_theme.py` asserting every label in `_SHOP_BUFFS`
  has a `BUFF_COLOURS` entry. The existing
  `test_every_pip_and_buff_colour_reaches_the_stylesheet` only checks the other
  direction (colour → stylesheet), so without this a newly-rotated tribe ships a
  colourless dot.
- `test_overlay_state.py`: a `Buffs` event carrying `shop` reaches
  `set_buffs`, and an unchanged `shop` does not re-render.
- `overlay/demo.py` gains a shop-buff group so `--overlay --demo` renders it.

## Verification

`.venv/bin/python -m pytest tests/` green, and `--overlay --demo` showing both
groups with correct dots.
