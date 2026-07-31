# Curating the Buffs panel, and giving turn economy a home

Date: 2026-07-30

## Problem

The Buffs panel (added whole, then extended in
[2026-07-24-tavern-shop-buffs-design.md](2026-07-24-tavern-shop-buffs-design.md))
shows every nonzero row it can find: up to four played-buff counters (Blood
Gem, Elemental, Pirate, Spell), every held tavern spell, and every tribe with
an active tavern shop buff (up to eleven possible rows). That is comprehensive
but noisy, and it has no idea gold or rerolls exist.

The author wants a fixed, curated set instead — Blood Gem, Spell Power, Undead
attack, Beetle Army, and the Elemental (Nomi) shop buff — plus a proper,
expanded treatment of two things the panel has never shown at all: gold banked
for next turn, and free rerolls.

Two of the five curated items are new mechanics with no reader today. Both
turned out to follow the exact `TAG_SCRIPT_DATA_NUM_1/2`-on-a-player-owned-
enchantment shape `read_shop_buffs` already reads, confirmed against the
author's own captured `Power.log` files:

```
TAG_CHANGE Entity=[entityName=Undead Bonus Attack Player Enchant [DNT]
  id=3443 zone=PLAY zonePos=0 cardId=BG25_011pe player=5]
  tag=TAG_SCRIPT_DATA_NUM_1 value=2
```

```
TAG_CHANGE Entity=[entityName=Beetle Army Player Enchant [DNT]
  id=17554 zone=PLAY zonePos=0 cardId=BG31_808pe player=11]
  tag=TAG_SCRIPT_DATA_NUM_1 value=148
TAG_CHANGE Entity=[entityName=Beetle Army Player Enchant [DNT]
  id=17554 zone=PLAY zonePos=0 cardId=BG31_808pe player=11]
  tag=TAG_SCRIPT_DATA_NUM_2 value=144
```

Undead only ever sets `NUM_1` — it is an attack-only buff (the card text is
"Give Attack to Undead"), unlike Beetle Army which sets both. This corrects an
existing comment in `state/game.py` claiming Undead has no player-wide counter
and is tracked per-minion; that was true of some earlier patch but is stale —
`BG25_011pe` is a direct, player-level accumulator, cheaper and more reliable
to read than summing bonus attack across every friendly Undead minion on
board.

Gold and rerolls are dedicated tags on the Player entity and its enchantments,
also confirmed against real games:

```
TAG_CHANGE Entity=Johan#12827 tag=BACON_PLAYER_EXTRA_GOLD_NEXT_TURN value=2
```

```
TAG_CHANGE Entity=[entityName=Bacon_Free_Refresh_Player_Ench [DNT] id=245
  zone=PLAY zonePos=0 cardId=Bacon_Free_Refresh_Player_Ench player=3]
  tag=BACON_FREE_REFRESH_COUNT value=1
```

The free-refresh count is mirrored onto the reroll button's own entity too
(`TB_BaconShop_8p_Reroll_Button`), which confirms it is the same "how many
free rerolls do I have right now" number the client itself shows on the
button — the right thing to read, rather than the separate cumulative
`BACON_NUM_FREE_REROLLS_USED` tag, whose reset semantics (per turn? per game?)
were not confirmed in the captured logs.

## Scope

Primarily a display change, in the same sense the 2026-07-24 spec was one:
`state/game.py` gains readers, the existing event/render spine carries the new
fields, nothing downstream of the panel changes.

### Not in scope

- **The simulator.** `_global_info()` and `sim/mapper.py` read the same
  `BACON_*_BUFF*VALUE` tag family independently and are untouched — a shop
  buff or a board buff shown in the panel is not sent differently because of
  this change. Matches the 2026-07-24 spec's own boundary.
- **Per-item visibility settings.** The curated set is fixed in code, not
  configurable. No new `Setting`, no new channel.
- **Opponent buffs.** The panel stays friendly-only, matching its existing
  scope.
- **Color-coding overdrawn (negative) gold.** `BACON_PLAYER_OVERDRAWN_GOLD_NEXT_TURN`
  exists and is read, but never appeared nonzero across four captured
  sessions. The value still displays correctly signed; a distinct warning
  color is deferred until it is known to matter.
- **Trimming `_SHOP_BUFFS` down to just Elemental.** The full eleven-tribe
  table stays in `state/game.py` as-is — it is documented, tested, and cheap
  to keep. Only the panel's own filtering narrows to Elemental; the reader
  keeps knowing about all eleven, in case a future ask brings one back.
- **Held tavern spells.** `read_active_spells` and the `spells` field are
  removed outright, not hidden — nothing else consumes them.

## Design

### 1. Reader — `state/game.py`

`_BUFF_TAGS` trims from four entries to two:

```python
_BUFF_TAGS = [
    ("Blood Gem", GameTag.BACON_BLOODGEMBUFFATKVALUE, GameTag.BACON_BLOODGEMBUFFHEALTHVALUE),
    ("Spell Power", GameTag.TAVERN_SPELL_ATTACK_INCREASE, GameTag.TAVERN_SPELL_HEALTH_INCREASE),
]
```

Pirate and the played-Elemental counter are dropped (label renamed only:
"Spell" becomes "Spell Power" for clarity — same tags, same read). `read_buffs`
itself is unchanged.

The entity-scan loop inside today's `read_shop_buffs` — filter by exact card
id, `CONTROLLER == player_id`, `ZONE == Zone.PLAY`, read
`TAG_SCRIPT_DATA_NUM_1/2` — is factored out into a shared helper, because
Undead and Beetle Army are the same shape with a different id-to-label table,
not a new pattern:

```python
def _read_script_buffs(game: Game, player_id: int, mapping: dict[str, str]) -> tuple[tuple[str, int, int], ...]:
    ...  # body moves here unchanged; read_shop_buffs becomes a one-line caller

_PLAYED_SCRIPT_BUFFS = {
    "BG25_011pe": "Undead",
    "BG31_808pe": "Beetle Army",
}

def read_played_buffs(game: Game, player_id: int) -> tuple[tuple[str, int, int], ...]:
    return _read_script_buffs(game, player_id, _PLAYED_SCRIPT_BUFFS)
```

`read_shop_buffs` keeps its existing signature and `_SHOP_BUFFS` table
verbatim; it becomes a thin call into `_read_script_buffs`.

Two new scalar readers, both simple Player-entity tag reads in the shape
`read_buffs` already uses:

```python
def read_gold_next_turn(game: Game, player_id: int) -> int:
    """Net gold banked for next turn; negative if overdrawn."""
    player = next((p for p in game.players if p.player_id == player_id), None)
    if player is None:
        return 0
    return (tag(player, GameTag.BACON_PLAYER_EXTRA_GOLD_NEXT_TURN)
            - tag(player, GameTag.BACON_PLAYER_OVERDRAWN_GOLD_NEXT_TURN))

def read_free_rerolls(game: Game, player_id: int) -> int:
    """Free rerolls available right now, mirrored from the reroll button's own count."""
    for e in game.entities:
        if (isinstance(e, Card)
                and e.card_id == "Bacon_Free_Refresh_Player_Ench"
                and tag(e, GameTag.CONTROLLER) == player_id
                and tag(e, GameTag.ZONE) == Zone.PLAY):
            return tag(e, GameTag.BACON_FREE_REFRESH_COUNT)
    return 0
```

`read_active_spells` and `_HELD_ZONES` are deleted; nothing else calls them.

### 2. Plumbing — `parse/events.py`, `parse/exporter.py`

`ev.Buffs` becomes:

```python
@dataclass
class Buffs:
    """Friendly player's curated buffs and turn economy.

    entries: (label, atk, health) board-buff counters — Blood Gem, Spell
             Power, Undead, Beetle Army — already baked into the board.
    shop:    (label, atk, health) tavern shop buffs, currently Elemental only
             — a minion in Bob's tavern is already carrying this, a buying
             decision, invisible to combat.
    gold_next_turn: net gold banked (negative if overdrawn), 0 if neither.
    free_rerolls:   free rerolls available this turn.
    """
    entries: tuple[tuple[str, int, int], ...] = ()
    shop: tuple[tuple[str, int, int], ...] = ()
    gold_next_turn: int = 0
    free_rerolls: int = 0
```

`maybe_emit_buffs` reads all four:

```python
def maybe_emit_buffs(self):
    fid = self.friendly_player_id()
    if fid is None:
        return
    entries = read_buffs(self.game, fid).entries + read_played_buffs(self.game, fid)
    shop = tuple(b for b in read_shop_buffs(self.game, fid) if b[0] == "Elemental")
    gold_next_turn = read_gold_next_turn(self.game, fid)
    free_rerolls = read_free_rerolls(self.game, fid)
    state = (entries, shop, gold_next_turn, free_rerolls)
    if state != self._buffs:
        self._buffs = state
        self._emit(ev.Buffs(entries=entries, shop=shop,
                             gold_next_turn=gold_next_turn, free_rerolls=free_rerolls))
```

Same trigger, same rate, same dedup-by-equality as today — only the tuple
being compared grows.

`OverlayState.buffs` becomes a 4-tuple
(`tuple[tuple, tuple, int, int] = ((), (), 0, 0)`); `overlay/app.py`'s
`case ev.Buffs(...)` and the game-reset `st.buffs = ((), (), 0, 0)` follow the
new shape.

### 3. Rendering — `overlay/window.py`, `overlay/theme.py`

```
┌─ Tavern Buffs ──────────────┐
│  ● Blood Gem        +2/+2   │
│  ● Spell Power      +1/+1   │
│  ● Undead            +4/+0  │
│  ● Beetle Army     +12/+8   │
│                              │
│  In Bob's Tavern             │
│  ● Elemental       +27/+27  │
│                              │
│  This Turn                   │
│  ● Gold Next Turn      +2   │
│  ● Free Reroll   1 available│
└──────────────────────────────┘
```

`set_buffs(entries, shop, gold_next_turn, free_rerolls)` appends board-buff
chips for `entries` (unchanged `_buff_chip`, no special-casing for Undead's
always-zero health side — `+N/+0` is accurate and consistent), then, only if
`shop` is non-empty, the existing `"In Bob's Tavern"` heading and its chip(s),
then, only if `gold_next_turn` or `free_rerolls` is nonzero, a new
`"This Turn"` heading (built with the existing `_buff_group` helper — no new
widget) followed by two more `_buff_chip` calls:

```python
if gold_next_turn or free_rerolls:
    self._buff_rows.append(self._buff_group("This Turn"))
    if gold_next_turn:
        sign = "+" if gold_next_turn > 0 else ""
        self._buff_rows.append(self._buff_chip("Gold Next Turn", f"{sign}{gold_next_turn}"))
    if free_rerolls:
        self._buff_rows.append(self._buff_chip("Free Reroll", f"{free_rerolls} available"))
```

`_buff_chip` already takes a label and a pre-formatted value string, so no new
row shape or CSS is needed for these two — they get a colored dot like every
other row. `_spell_chip` and the `.spell` CSS class are deleted.
`_set_content("buffs", ...)`'s "any row present" gate is unchanged in spirit;
it now looks at all four fields instead of three.

`BUFF_COLOURS` changes:

- `"Spell"` → `"Spell Power"` (same hex, `#a97fd0`, key renamed to match).
- `"Pirate"` removed — no reader emits that label anymore.
- `"Undead"` kept as-is (`#b9b3a0`) — originally added for the shop-buff-group
  Undead tribe, which no longer surfaces (shop is Elemental-only now); reused
  for the new board-buff Undead entry. Same visual identity, different tag
  source underneath, worth knowing if grepping for why the key predates this
  feature.
- `"Beetle Army"` new: `#b07a3f`, a bronze/carapace tone distinct from the
  remaining live colors.
- `"Gold Next Turn"` new: `#f4d47a` — the same hex as the `$gold` theme token,
  so the dot visually ties to the currency identity used everywhere else in
  the overlay.
- `"Free Reroll"` new: `#5fa3d0`, a cool blue for "opportunity." This and
  Beetle Army's hex are starting points; final tuning happens by eye against
  their neighbors during the `--overlay --demo` check below, not pinned
  further here.

### 4. Testing

Mirroring the 2026-07-24 spec's own test shape:

- `read_played_buffs` against synthetic entities: Undead (attack-only, health
  stays 0), Beetle Army (both), another controller's enchantment ignored, a
  zero-valued one suppressed.
- `read_gold_next_turn`: positive, negative (overdrawn), both zero, and both
  nonzero at once (net).
- `read_free_rerolls`: present and nonzero, present and zero, absent.
- `test_overlay_theme.py`: extend the existing colour-coverage assertions for
  the renamed/added `BUFF_COLOURS` keys.
- `test_overlay_state.py`: a `Buffs` event carrying the new fields reaches
  `set_buffs`; an unchanged tuple does not re-render.
- `test_shop_buffs.py`, `test_overlay_buffs_panel.py`, `test_tailer.py`:
  updated for the trimmed `entries`/`shop` shape and the removed `spells`
  field.
- `overlay/demo.py`'s seeded `Buffs` event gains Undead, Beetle Army,
  `gold_next_turn`, and `free_rerolls` sample values so `--overlay --demo`
  renders every new row in one pass.

## Verification

`.venv/bin/python -m pytest tests/` green, and `--overlay --demo` showing all
three groups — board buffs, In Bob's Tavern, This Turn — with correct dots and
no leftover spell chips.
