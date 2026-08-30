# Buffs Panel Curation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Curate the Buffs panel down to a fixed set (Blood Gem, Spell Power, Undead, Beetle Army, Elemental) and add a "This Turn" economy section (gold banked for next turn, free rerolls), replacing the current show-everything-nonzero panel.

**Architecture:** `state/game.py` gains three new readers (`read_played_buffs`, `read_gold_next_turn`, `read_free_rerolls`) and trims/renames the existing `_BUFF_TAGS`. The existing `ev.Buffs` → `maybe_emit_buffs` → `OverlayState.buffs` → `OverlayWindow.set_buffs` spine carries the new fields through unchanged in shape, growing incrementally task by task. Held tavern spells are removed outright. The Elemental-only curation of tavern shop buffs happens in the render layer (`set_buffs`), not the exporter, so `ev.Buffs.shop` stays a faithful full readout.

**Tech Stack:** Python 3.14 (system interpreter via `.venv --system-site-packages`), GTK4 (`gi.repository.Gtk`), pytest.

**Spec:** [docs/superpowers/specs/2026-07-30-buffs-panel-curation-design.md](../specs/2026-07-30-buffs-panel-curation-design.md)

## Global Constraints

- Run `.venv/bin/python -m pytest tests/` (the whole suite) after every task — it is ~320 tests in ~23s, fast enough that running a subset and calling it done is never justified.
- No comments beyond WHY. Never explain what code does when the identifiers already say so.
- Do not touch `_global_info()` or `sim/mapper.py` — this feature is display-only and must not change what reaches the simulator.
- No new `Setting`, no new config channel — the curated set is fixed in code.
- Commit messages explain the failure/why, not the diff, matching this repo's existing log.
- `_read_script_buffs`, `_PLAYED_SCRIPT_BUFFS`, `read_played_buffs`, `read_gold_next_turn`, and `read_free_rerolls` are the exact names later tasks and tests depend on — do not rename them mid-plan.

---

## Task 1: Trim played-buff counters to Blood Gem and Spell Power

**Files:**
- Modify: `bgtracker/state/game.py:124-132` (the `_BUFF_TAGS` list and its comment)
- Modify: `tests/synthetic.py` (add shared pipeline-test helpers)
- Modify: `tests/test_shop_buffs.py` (switch to the shared helpers)
- Create: `tests/test_buffs_curation.py`

**Interfaces:**
- Produces: `tests.synthetic.recruit_phase_game() -> LogBuilder`, `tests.synthetic.feed_buffs(builder: LogBuilder) -> list[ev.Event]`, `tests.synthetic.buffs_from(builder: LogBuilder) -> ev.Buffs | None` — every later task's tests in `test_buffs_curation.py` use these three.

`tests/test_shop_buffs.py` currently defines `shop_buff_game()`/`feed()`/`buffs_from()` locally. This task moves them to `tests/synthetic.py` under neutral names, because Task 3 onward needs the identical harness in a second file and duplicating a non-trivial pipeline-feeding helper across two test files is the kind of duplication worth factoring immediately, not after a third copy appears.

- [ ] **Step 1: Move the shared test helpers into `tests/synthetic.py`**

Add these imports to the top of `tests/synthetic.py`, right after the existing `from __future__ import annotations`:

```python
from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor
```

Append this to the end of `tests/synthetic.py` (after `minimal_bg_game()`):

```python
def recruit_phase_game() -> LogBuilder:
    """A bare two-player recruit phase: heroes in play, friendly player
    established via a revealed hand card. Callers add whatever entities or
    tag changes they need to test."""
    b = LogBuilder()
    b.add("CREATE_GAME")
    b.add("GameEntity EntityID=1", indent=1)
    b.add("tag=TURN value=1", indent=2)
    b.add("Player EntityID=2 PlayerID=1 GameAccountId=[hi=1 lo=1]", indent=1)
    b.add("Player EntityID=3 PlayerID=2 GameAccountId=[hi=1 lo=2]", indent=1)
    b.entity(4, "TB_BaconShop_HERO_11", CARDTYPE="HERO", ZONE="PLAY", CONTROLLER=1,
             HEALTH=40, PLAYER_TECH_LEVEL=2)
    b.entity(5, "TB_BaconShop_HERO_22", CARDTYPE="HERO", ZONE="PLAY", CONTROLLER=2,
             HEALTH=30, PLAYER_TECH_LEVEL=1)
    # revealed card in OUR hand -> friendly-player detection says player 1
    b.entity(6, "BG_EX1_506", CARDTYPE="MINION", ZONE="HAND", CONTROLLER=1)
    return b


def feed_buffs(builder: LogBuilder) -> list:
    """Feed the log, with a trailing packet so the last one is exported.

    A packet is only exported once something follows it — the tailer has to
    assume a final line may still be half-written.
    """
    builder.tag_change("GameEntity", "TURN", 2)
    return LiveGameProcessor().feed(builder.lines)


def buffs_from(builder: LogBuilder) -> ev.Buffs | None:
    emitted = [e for e in feed_buffs(builder) if isinstance(e, ev.Buffs)]
    return emitted[-1] if emitted else None
```

- [ ] **Step 2: Point `tests/test_shop_buffs.py` at the shared helpers**

Replace the file's header (everything before the first `def test_`) with:

```python
"""Tavern shop buffs: what a minion in Bob's tavern already carries.

Nomi and friends do not write a player *tag* the way Blood Gem does. They feed
a per-tribe `BG_ShopBuff_<tribe>` enchantment attached to the player entity,
whose running total lives in TAG_SCRIPT_DATA_NUM_1/2. Several cards stack into
one entity, so this is a read of a total rather than a count of triggers.
"""

from bgtracker.parse import events as ev

from .synthetic import buffs_from, feed_buffs, recruit_phase_game
```

Then replace every `shop_buff_game()` call in the file's test bodies with `recruit_phase_game()` (six occurrences), and in `test_a_shop_buff_that_has_not_moved_is_not_re_reported`, change `feed(b)` to `feed_buffs(b)`.

- [ ] **Step 3: Run the existing shop-buff tests to confirm the move didn't break anything**

Run: `.venv/bin/python -m pytest tests/test_shop_buffs.py -v`
Expected: all 6 tests PASS, unchanged behavior.

- [ ] **Step 4: Write the failing test for the trim**

Create `tests/test_buffs_curation.py`:

```python
"""Buffs panel curation: a fixed set of played-buff counters and turn
economy, replacing the old show-everything-nonzero panel.

See docs/superpowers/specs/2026-07-30-buffs-panel-curation-design.md for the
log evidence behind every reader in this module.
"""

from .synthetic import buffs_from, recruit_phase_game


def test_blood_gem_and_spell_power_are_read_from_the_player():
    b = recruit_phase_game()
    b.tag_change(2, "BACON_BLOODGEMBUFFATKVALUE", 2)
    b.tag_change(2, "BACON_BLOODGEMBUFFHEALTHVALUE", 2)
    b.tag_change(2, "TAVERN_SPELL_ATTACK_INCREASE", 1)
    b.tag_change(2, "TAVERN_SPELL_HEALTH_INCREASE", 1)
    assert buffs_from(b).entries == (("Blood Gem", 2, 2), ("Spell Power", 1, 1))


def test_pirate_and_played_elemental_no_longer_produce_entries():
    """Both were rows before this feature curated the panel down; neither
    tag is in _BUFF_TAGS anymore."""
    b = recruit_phase_game()
    b.tag_change(2, "BACON_PIRATE_BUFFATKVALUE", 3)
    b.tag_change(2, "BACON_PIRATE_BUFFHEALTHVALUE", 3)
    b.tag_change(2, "BACON_ELEMENTAL_BUFFATKVALUE", 2)
    b.tag_change(2, "BACON_ELEMENTAL_BUFFHEALTHVALUE", 2)
    buffs = buffs_from(b)
    assert buffs is None or buffs.entries == ()
```

- [ ] **Step 5: Run the new test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_buffs_curation.py -v`
Expected: `test_blood_gem_and_spell_power_are_read_from_the_player` FAILS — actual entries still include no "Spell Power" label (it's "Spell" today). `test_pirate_and_played_elemental_no_longer_produce_entries` FAILS — Pirate and Elemental still produce entries today.

- [ ] **Step 6: Trim and rename `_BUFF_TAGS`**

In `bgtracker/state/game.py`, replace lines 124-132:

```python
# Player-wide "tavern buff" counters that accumulate across a game, stored as
# tags on the Player entity. Each: (label, attack-tag, health-tag). Undead has
# no equivalent player counter (tracked per-minion), so it isn't here.
_BUFF_TAGS = [
    ("Blood Gem", GameTag.BACON_BLOODGEMBUFFATKVALUE, GameTag.BACON_BLOODGEMBUFFHEALTHVALUE),
    ("Elemental", GameTag.BACON_ELEMENTAL_BUFFATKVALUE, GameTag.BACON_ELEMENTAL_BUFFHEALTHVALUE),
    ("Pirate", GameTag.BACON_PIRATE_BUFFATKVALUE, GameTag.BACON_PIRATE_BUFFHEALTHVALUE),
    ("Spell", GameTag.TAVERN_SPELL_ATTACK_INCREASE, GameTag.TAVERN_SPELL_HEALTH_INCREASE),
]
```

with:

```python
# Player-wide "tavern buff" counters that accumulate across a game, stored as
# dedicated tags on the Player entity. Each: (label, attack-tag, health-tag).
# Curated to what the Buffs panel shows — Pirate and the played-Elemental
# counter used to be here too; see
# docs/superpowers/specs/2026-07-30-buffs-panel-curation-design.md.
_BUFF_TAGS = [
    ("Blood Gem", GameTag.BACON_BLOODGEMBUFFATKVALUE, GameTag.BACON_BLOODGEMBUFFHEALTHVALUE),
    ("Spell Power", GameTag.TAVERN_SPELL_ATTACK_INCREASE, GameTag.TAVERN_SPELL_HEALTH_INCREASE),
]
```

- [ ] **Step 7: Run the new test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_buffs_curation.py -v`
Expected: both tests PASS.

- [ ] **Step 8: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: all PASS. (`test_every_buff_label_the_reader_can_emit_has_a_colour` in `test_overlay_theme.py` still passes here — `_BUFF_TAGS` now yields a *subset* of the labels `BUFF_COLOURS` already has keys for, which still satisfies `labels <= set(theme.BUFF_COLOURS)`.)

- [ ] **Step 9: Commit**

```bash
git add bgtracker/state/game.py tests/synthetic.py tests/test_shop_buffs.py tests/test_buffs_curation.py
git commit -m "$(cat <<'EOF'
Trim played-buff counters to Blood Gem and Spell Power

The Buffs panel showed four counters (Blood Gem, Elemental, Pirate,
Spell) whenever any were nonzero. Per the curation spec, only Blood
Gem and the renamed Spell Power stay; Pirate and the played-Elemental
counter are dropped (the Elemental row the panel keeps is the separate
Nomi tavern-shop buff, unaffected by this change).

Moved the shop-buff test harness (recruit_phase_game/feed_buffs/
buffs_from) into tests/synthetic.py so the new test_buffs_curation.py
can share it instead of duplicating a non-trivial pipeline-feeding
helper.
EOF
)"
```

---

## Task 2: Share the script-data buff reader between shop and played buffs

**Files:**
- Modify: `bgtracker/state/game.py:189-211` (`read_shop_buffs`)

**Interfaces:**
- Produces: `_read_script_buffs(game: Game, player_id: int, mapping: dict[str, str]) -> tuple[tuple[str, int, int], ...]` — Task 3's `read_played_buffs` calls this directly.
- Consumes: nothing new; `read_shop_buffs`'s existing signature and behavior are unchanged.

This is a pure refactor — Task 3 needs Undead and Beetle Army read with the exact same "player-owned script-data enchantment, exact card id, `CONTROLLER`+`ZONE==PLAY`" scan `read_shop_buffs` already does, just against a different id-to-label table. Extracting the shared loop now avoids writing it twice. There is no new behavior, so there is no new test — the existing `test_shop_buffs.py` suite is the safety net.

- [ ] **Step 1: Confirm the safety net is green before touching anything**

Run: `.venv/bin/python -m pytest tests/test_shop_buffs.py -v`
Expected: all 6 tests PASS (this is the baseline the refactor must not move).

- [ ] **Step 2: Extract `_read_script_buffs` and make `read_shop_buffs` a thin caller**

In `bgtracker/state/game.py`, replace the current `read_shop_buffs` function body:

```python
def read_shop_buffs(game: Game, player_id: int) -> tuple[tuple[str, int, int], ...]:
    """Friendly tavern-wide buffs, as (label, attack, health).

    Declared order, not discovery order, so a row keeps its place in the panel
    as its value climbs.
    """
    found: dict[str, tuple[int, int]] = {}
    for e in game.entities:
        if (
            isinstance(e, Card)
            and e.card_id in _SHOP_BUFFS
            and tag(e, GameTag.CONTROLLER) == player_id
            and tag(e, GameTag.ZONE) == Zone.PLAY
        ):
            atk = tag(e, GameTag.TAG_SCRIPT_DATA_NUM_1)
            hp = tag(e, GameTag.TAG_SCRIPT_DATA_NUM_2)
            if atk or hp:
                found[e.card_id] = (atk, hp)
    return tuple(
        (label, *found[card_id])
        for card_id, label in _SHOP_BUFFS.items()
        if card_id in found
    )
```

with:

```python
def _read_script_buffs(
    game: Game, player_id: int, mapping: dict[str, str]
) -> tuple[tuple[str, int, int], ...]:
    """Read (label, attack, health) off player-owned script-data enchantments.

    Shared shape behind both the tavern shop buffs and the played board-wide
    buffs (Undead, Beetle Army): one enchantment per source, attached to the
    player, holding a running total in TAG_SCRIPT_DATA_NUM_1/2. `mapping` is
    the only thing that differs between callers.
    """
    found: dict[str, tuple[int, int]] = {}
    for e in game.entities:
        if (
            isinstance(e, Card)
            and e.card_id in mapping
            and tag(e, GameTag.CONTROLLER) == player_id
            and tag(e, GameTag.ZONE) == Zone.PLAY
        ):
            atk = tag(e, GameTag.TAG_SCRIPT_DATA_NUM_1)
            hp = tag(e, GameTag.TAG_SCRIPT_DATA_NUM_2)
            if atk or hp:
                found[e.card_id] = (atk, hp)
    return tuple(
        (label, *found[card_id])
        for card_id, label in mapping.items()
        if card_id in found
    )


def read_shop_buffs(game: Game, player_id: int) -> tuple[tuple[str, int, int], ...]:
    """Friendly tavern-wide buffs, as (label, attack, health).

    Declared order, not discovery order, so a row keeps its place in the panel
    as its value climbs.
    """
    return _read_script_buffs(game, player_id, _SHOP_BUFFS)
```

- [ ] **Step 3: Run the same tests to verify nothing moved**

Run: `.venv/bin/python -m pytest tests/test_shop_buffs.py -v`
Expected: same 6 tests PASS, byte-identical results to Step 1.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bgtracker/state/game.py
git commit -m "$(cat <<'EOF'
Factor the script-data buff scan out of read_shop_buffs

Undead and Beetle Army (next commit) need the identical
player-owned-enchantment scan read_shop_buffs already does, just
against a different id-to-label table. Pure refactor: read_shop_buffs
keeps its signature and docstring, existing tests unchanged.
EOF
)"
```

---

## Task 3: Read Undead and Beetle Army as played buffs

**Files:**
- Modify: `bgtracker/state/game.py` (add `_PLAYED_SCRIPT_BUFFS` and `read_played_buffs` after `read_shop_buffs`)
- Modify: `bgtracker/parse/exporter.py` (wire into `maybe_emit_buffs`'s `entries`)
- Modify: `tests/test_buffs_curation.py`

**Interfaces:**
- Consumes: `_read_script_buffs` from Task 2.
- Produces: `read_played_buffs(game: Game, player_id: int) -> tuple[tuple[str, int, int], ...]` — consumed by `maybe_emit_buffs` in this same task, and by no one else.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_buffs_curation.py`:

```python
def test_undead_bonus_attack_is_read_as_a_played_buff():
    b = recruit_phase_game()
    b.entity(20, "BG25_011pe", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=4)
    assert buffs_from(b).entries == (("Undead", 4, 0),)


def test_beetle_army_is_read_as_a_played_buff():
    b = recruit_phase_game()
    b.entity(20, "BG31_808pe", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=12,
             TAG_SCRIPT_DATA_NUM_2=8)
    assert buffs_from(b).entries == (("Beetle Army", 12, 8),)


def test_another_controllers_played_script_buff_is_not_ours():
    b = recruit_phase_game()
    b.entity(20, "BG25_011pe", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=2, ATTACHED=3, TAG_SCRIPT_DATA_NUM_1=4)
    buffs = buffs_from(b)
    assert buffs is None or buffs.entries == ()


def test_a_zero_valued_played_script_buff_makes_no_entry():
    """The enchantment is created before the first trigger sets its value."""
    b = recruit_phase_game()
    b.entity(20, "BG31_808pe", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2)
    buffs = buffs_from(b)
    assert buffs is None or buffs.entries == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_buffs_curation.py -v`
Expected: `test_undead_bonus_attack_is_read_as_a_played_buff` and `test_beetle_army_is_read_as_a_played_buff` FAIL with an assertion mismatch (`entries == ()` today, nothing reads `BG25_011pe`/`BG31_808pe` yet). The other two already pass trivially (nothing produces entries for them either way) — that's fine, they exist to document the requirement and will keep passing.

- [ ] **Step 3: Add `_PLAYED_SCRIPT_BUFFS` and `read_played_buffs`**

In `bgtracker/state/game.py`, immediately after the `read_shop_buffs` function (end of Task 2's edit) and before the `# Persistent tavern SPELLS...` comment block, insert:

```python
# Buffs that accumulate on the board itself, in the same per-player
# TAG_SCRIPT_DATA_NUM_1/2 enchantment shape read_shop_buffs already reads — a
# different quantity from the tavern buffs above (already baked into your
# minions, not a purchasing decision) but mechanically the same read.
#
# Undead only ever sets NUM_1: the card text is "Give Attack to Undead", so
# health stays 0 rather than absent. Beetle Army sets both.
_PLAYED_SCRIPT_BUFFS = {
    "BG25_011pe": "Undead",
    "BG31_808pe": "Beetle Army",
}


def read_played_buffs(game: Game, player_id: int) -> tuple[tuple[str, int, int], ...]:
    """Friendly played board-wide buffs not covered by a dedicated GameTag."""
    return _read_script_buffs(game, player_id, _PLAYED_SCRIPT_BUFFS)
```

- [ ] **Step 4: Wire it into `maybe_emit_buffs`**

In `bgtracker/parse/exporter.py`, add `read_played_buffs` to the import from `bgtracker.state.game` (keep alphabetical order):

```python
from bgtracker.state.game import (
    BoardSnapshot,
    project_player_board,
    read_active_spells,
    read_buffs,
    read_played_buffs,
    read_shop_buffs,
    tag,
)
```

Change the first line of `maybe_emit_buffs`'s body:

```python
        entries = read_buffs(self.game, fid).entries
```

to:

```python
        entries = read_buffs(self.game, fid).entries + read_played_buffs(self.game, fid)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_buffs_curation.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add bgtracker/state/game.py bgtracker/parse/exporter.py tests/test_buffs_curation.py
git commit -m "$(cat <<'EOF'
Read Undead and Beetle Army as played buffs

Both are player-level accumulators in BG25_011pe/BG31_808pe, the same
TAG_SCRIPT_DATA_NUM_1/2-on-an-enchantment shape as the tavern shop
buffs — confirmed against captured Power.log files. This corrects an
existing comment claiming Undead has no player-wide counter and is
tracked per-minion; BG25_011pe is direct and player-level, cheaper
than summing bonus attack across every friendly Undead minion. Undead
only ever sets NUM_1 (the card text is "Give Attack to Undead"), so it
always reads with health 0.

Wired into maybe_emit_buffs's `entries` immediately — this already
reaches the live overlay, ahead of the panel's other curation changes.
EOF
)"
```

---

## Task 4: Read gold banked for next turn

**Files:**
- Modify: `bgtracker/parse/events.py` (`Buffs` dataclass gains `gold_next_turn`)
- Modify: `bgtracker/state/game.py` (add `read_gold_next_turn`)
- Modify: `bgtracker/parse/exporter.py` (wire into `maybe_emit_buffs`)
- Modify: `tests/test_buffs_curation.py`

**Interfaces:**
- Produces: `read_gold_next_turn(game: Game, player_id: int) -> int`, and `ev.Buffs.gold_next_turn: int = 0`.

Adding a new dataclass field with a default does not break `overlay/app.py`'s existing `case ev.Buffs(entries=e, spells=sp, shop=shop):` pattern — unmatched fields are simply not bound. `gold_next_turn` exists on the event after this task but isn't surfaced in the overlay until Task 6; that's fine, it's independently correct and independently tested through the pipeline in the meantime.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_buffs_curation.py`:

```python
def test_gold_banked_for_next_turn_is_read():
    b = recruit_phase_game()
    b.tag_change(2, "BACON_PLAYER_EXTRA_GOLD_NEXT_TURN", 2)
    assert buffs_from(b).gold_next_turn == 2


def test_overdrawn_gold_next_turn_reads_negative():
    b = recruit_phase_game()
    b.tag_change(2, "BACON_PLAYER_OVERDRAWN_GOLD_NEXT_TURN", 1)
    assert buffs_from(b).gold_next_turn == -1


def test_extra_and_overdrawn_gold_next_turn_net_together():
    b = recruit_phase_game()
    b.tag_change(2, "BACON_PLAYER_EXTRA_GOLD_NEXT_TURN", 3)
    b.tag_change(2, "BACON_PLAYER_OVERDRAWN_GOLD_NEXT_TURN", 1)
    assert buffs_from(b).gold_next_turn == 2


def test_no_gold_next_turn_tag_reads_zero():
    b = recruit_phase_game()
    buffs = buffs_from(b)
    assert buffs is None or buffs.gold_next_turn == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_buffs_curation.py -v`
Expected: the first three FAIL — `ev.Buffs` has no `gold_next_turn` attribute yet, so `buffs_from(b).gold_next_turn` raises `AttributeError`. The fourth passes trivially either way.

- [ ] **Step 3: Add the field to `ev.Buffs`**

In `bgtracker/parse/events.py`, replace the `Buffs` dataclass:

```python
@dataclass(frozen=True)
class Buffs:
    """Friendly player's tavern buffs and held tavern spells/trinkets.

    entries: (label, atk, health) accumulating counters, applied when a minion
             is played and so already baked into the board.
    spells:  card ids of persistent tavern spells/trinkets held.
    shop:    (label, atk, health) buffs a minion already carries while it sits
             in Bob's tavern — a buying decision, invisible to combat.
    """

    entries: tuple[tuple[str, int, int], ...]
    spells: tuple[str, ...] = ()
    shop: tuple[tuple[str, int, int], ...] = ()
```

with:

```python
@dataclass(frozen=True)
class Buffs:
    """Friendly player's tavern buffs, held tavern spells/trinkets, and turn economy.

    entries: (label, atk, health) accumulating counters, applied when a minion
             is played and so already baked into the board.
    spells:  card ids of persistent tavern spells/trinkets held.
    shop:    (label, atk, health) buffs a minion already carries while it sits
             in Bob's tavern — a buying decision, invisible to combat.
    gold_next_turn: net gold banked for next turn; negative if overdrawn.
    """

    entries: tuple[tuple[str, int, int], ...]
    spells: tuple[str, ...] = ()
    shop: tuple[tuple[str, int, int], ...] = ()
    gold_next_turn: int = 0
```

- [ ] **Step 4: Add `read_gold_next_turn`**

In `bgtracker/state/game.py`, immediately after `read_played_buffs` (Task 3's addition), insert:

```python
def read_gold_next_turn(game: Game, player_id: int) -> int:
    """Net gold banked for next turn; negative if overdrawn."""
    player = next((p for p in game.players if p.player_id == player_id), None)
    if player is None:
        return 0
    return (tag(player, GameTag.BACON_PLAYER_EXTRA_GOLD_NEXT_TURN)
            - tag(player, GameTag.BACON_PLAYER_OVERDRAWN_GOLD_NEXT_TURN))
```

- [ ] **Step 5: Wire it into `maybe_emit_buffs`**

In `bgtracker/parse/exporter.py`, replace the `bgtracker.state.game` import block:

```python
from bgtracker.state.game import (
    BoardSnapshot,
    project_player_board,
    read_active_spells,
    read_buffs,
    read_played_buffs,
    read_shop_buffs,
    tag,
)
```

with:

```python
from bgtracker.state.game import (
    BoardSnapshot,
    project_player_board,
    read_active_spells,
    read_buffs,
    read_gold_next_turn,
    read_played_buffs,
    read_shop_buffs,
    tag,
)
```

Then replace the body of `maybe_emit_buffs`:

```python
    def maybe_emit_buffs(self):
        fid = self.friendly_player_id()
        if fid is None:
            return
        entries = read_buffs(self.game, fid).entries + read_played_buffs(self.game, fid)
        spells = read_active_spells(self.game, fid)
        shop = read_shop_buffs(self.game, fid)
        if (entries, spells, shop) != self._buffs:
            self._buffs = (entries, spells, shop)
            self._emit(ev.Buffs(entries=entries, spells=spells, shop=shop))
```

with:

```python
    def maybe_emit_buffs(self):
        fid = self.friendly_player_id()
        if fid is None:
            return
        entries = read_buffs(self.game, fid).entries + read_played_buffs(self.game, fid)
        spells = read_active_spells(self.game, fid)
        shop = read_shop_buffs(self.game, fid)
        gold_next_turn = read_gold_next_turn(self.game, fid)
        state = (entries, spells, shop, gold_next_turn)
        if state != self._buffs:
            self._buffs = state
            self._emit(ev.Buffs(entries=entries, spells=spells, shop=shop,
                                 gold_next_turn=gold_next_turn))
```

Also update the init default at `bgtracker/parse/exporter.py:92`, from:

```python
        self._buffs: tuple = ((), (), ())
```

to:

```python
        self._buffs: tuple = ((), (), (), 0)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_buffs_curation.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add bgtracker/parse/events.py bgtracker/state/game.py bgtracker/parse/exporter.py tests/test_buffs_curation.py
git commit -m "$(cat <<'EOF'
Read gold banked for next turn

BACON_PLAYER_EXTRA_GOLD_NEXT_TURN and its overdrawn counterpart are
dedicated tags on the Player entity, confirmed against a captured
Power.log (value=2, cleared to 0 the following turn). Net signed value
so a future overdrawn effect displays correctly without extra plumbing.

Not yet surfaced in the overlay - the field exists on ev.Buffs and is
tested through the full pipeline, but OverlayWindow.set_buffs doesn't
accept it until the panel rendering task.
EOF
)"
```

---

## Task 5: Read free rerolls available this turn

**Files:**
- Modify: `bgtracker/parse/events.py` (`Buffs` dataclass gains `free_rerolls`)
- Modify: `bgtracker/state/game.py` (add `read_free_rerolls`)
- Modify: `bgtracker/parse/exporter.py` (wire into `maybe_emit_buffs`)
- Modify: `tests/test_buffs_curation.py`

**Interfaces:**
- Produces: `read_free_rerolls(game: Game, player_id: int) -> int`, and `ev.Buffs.free_rerolls: int = 0`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_buffs_curation.py`:

```python
def test_free_rerolls_available_this_turn_are_read():
    b = recruit_phase_game()
    b.entity(20, "Bacon_Free_Refresh_Player_Ench", CARDTYPE="ENCHANTMENT",
             ZONE="PLAY", CONTROLLER=1, ATTACHED=2, BACON_FREE_REFRESH_COUNT=1)
    assert buffs_from(b).free_rerolls == 1


def test_another_controllers_free_reroll_enchantment_is_not_ours():
    b = recruit_phase_game()
    b.entity(20, "Bacon_Free_Refresh_Player_Ench", CARDTYPE="ENCHANTMENT",
             ZONE="PLAY", CONTROLLER=2, ATTACHED=3, BACON_FREE_REFRESH_COUNT=1)
    buffs = buffs_from(b)
    assert buffs is None or buffs.free_rerolls == 0


def test_no_free_reroll_enchantment_reads_zero():
    b = recruit_phase_game()
    buffs = buffs_from(b)
    assert buffs is None or buffs.free_rerolls == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_buffs_curation.py -v`
Expected: `test_free_rerolls_available_this_turn_are_read` FAILS with `AttributeError: 'Buffs' object has no attribute 'free_rerolls'`. The other two pass trivially.

- [ ] **Step 3: Add the field to `ev.Buffs`**

In `bgtracker/parse/events.py`, update the `Buffs` dataclass docstring and fields:

```python
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
```

- [ ] **Step 4: Add `read_free_rerolls`**

In `bgtracker/state/game.py`, immediately after `read_gold_next_turn` (Task 4's addition), insert:

```python
def read_free_rerolls(game: Game, player_id: int) -> int:
    """Free rerolls available right now, mirrored onto the reroll button itself."""
    for e in game.entities:
        if (
            isinstance(e, Card)
            and e.card_id == "Bacon_Free_Refresh_Player_Ench"
            and tag(e, GameTag.CONTROLLER) == player_id
            and tag(e, GameTag.ZONE) == Zone.PLAY
        ):
            return tag(e, GameTag.BACON_FREE_REFRESH_COUNT)
    return 0
```

- [ ] **Step 5: Wire it into `maybe_emit_buffs`**

In `bgtracker/parse/exporter.py`, replace the `bgtracker.state.game` import block:

```python
from bgtracker.state.game import (
    BoardSnapshot,
    project_player_board,
    read_active_spells,
    read_buffs,
    read_gold_next_turn,
    read_played_buffs,
    read_shop_buffs,
    tag,
)
```

with:

```python
from bgtracker.state.game import (
    BoardSnapshot,
    project_player_board,
    read_active_spells,
    read_buffs,
    read_free_rerolls,
    read_gold_next_turn,
    read_played_buffs,
    read_shop_buffs,
    tag,
)
```

Then replace `maybe_emit_buffs`'s body:

```python
    def maybe_emit_buffs(self):
        fid = self.friendly_player_id()
        if fid is None:
            return
        entries = read_buffs(self.game, fid).entries + read_played_buffs(self.game, fid)
        spells = read_active_spells(self.game, fid)
        shop = read_shop_buffs(self.game, fid)
        gold_next_turn = read_gold_next_turn(self.game, fid)
        free_rerolls = read_free_rerolls(self.game, fid)
        state = (entries, spells, shop, gold_next_turn, free_rerolls)
        if state != self._buffs:
            self._buffs = state
            self._emit(ev.Buffs(entries=entries, spells=spells, shop=shop,
                                 gold_next_turn=gold_next_turn, free_rerolls=free_rerolls))
```

Update the init default at `bgtracker/parse/exporter.py:92` (now `((), (), (), 0)` from Task 4) to:

```python
        self._buffs: tuple = ((), (), (), 0, 0)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_buffs_curation.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add bgtracker/parse/events.py bgtracker/state/game.py bgtracker/parse/exporter.py tests/test_buffs_curation.py
git commit -m "$(cat <<'EOF'
Read free rerolls available this turn

Bacon_Free_Refresh_Player_Ench carries BACON_FREE_REFRESH_COUNT, the
same number mirrored onto the reroll button's own entity
(TB_BaconShop_8p_Reroll_Button) in a captured Power.log - confirming
it's the number the client itself shows, not the separate cumulative
BACON_NUM_FREE_REROLLS_USED tag whose reset semantics weren't
confirmed in the captured logs.
EOF
)"
```

---

## Task 6: Retire held tavern spells; surface the curated set and turn economy in the overlay

**Files:**
- Modify: `bgtracker/parse/events.py` (`Buffs` loses `spells`)
- Modify: `bgtracker/state/game.py` (delete `read_active_spells`, `_HELD_ZONES`)
- Modify: `bgtracker/parse/exporter.py` (stop reading/emitting `spells`)
- Modify: `bgtracker/overlay/app.py` (case pattern + reset)
- Modify: `bgtracker/overlay/model.py` (`OverlayState.buffs` shape)
- Modify: `bgtracker/overlay/window.py` (`set_buffs` rewritten, `_spell_chip` deleted)
- Modify: `tests/test_overlay_buffs_panel.py` (rewritten for the new signature)
- Modify: `tests/test_overlay_state.py` (one assertion, one new test)

**Interfaces:**
- Consumes: `read_gold_next_turn`, `read_free_rerolls` (Tasks 4-5), `read_played_buffs` (Task 3).
- Produces: `OverlayWindow.set_buffs(entries, shop=(), gold_next_turn=0, free_rerolls=0) -> None` — the final signature; no later task changes it.

This is one atomic slice, not several: `ev.Buffs` losing the `spells` field, `app.py`'s pattern match, and `window.py`'s `set_buffs` signature all have to move together, because `overlay/app.py`'s `case ev.Buffs(entries=e, spells=sp, shop=shop):` would silently stop matching (and `window.set_buffs(*state.buffs)` would then call the wrong positional slots) if any one of the three lagged behind the others.

- [ ] **Step 1: Write the failing tests**

Replace all of `tests/test_overlay_buffs_panel.py` with:

```python
"""The Buffs panel renders three groups that mean different things.

The played-buff rows are already baked into the board; the tavern-buff row is
what a minion in Bob's tavern is carrying before you buy it; the economy rows
are turn-scoped state that has nothing to do with a minion at all. Read as one
flat list the first two look like a single stacking number, so the shop group
gets its own heading — and `shop` itself arrives carrying every tribe the
reader recognises, curated down to Elemental here because that is what the
panel shows, not what the reader returns.

No display: `set_buffs` only appends to a Gtk.Box, so the window is built with
`__new__` and the handful of fields it touches are set directly.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
pytest.importorskip("gi.repository.Gtk4LayerShell")
from gi.repository import Gtk  # noqa: E402

from bgtracker.overlay import theme  # noqa: E402
from bgtracker.overlay.window import OverlayWindow  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def gtk():
    theme.register_fonts()
    Gtk.init()


@pytest.fixture
def win():
    w = OverlayWindow.__new__(OverlayWindow)
    w.scale = 1.0
    w.buffs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    w._buff_rows = []
    w.shown = {}
    w._set_content = lambda name, has: w.shown.__setitem__(name, has)
    return w


def rows(win) -> list[str]:
    """Each rendered row flattened to text, in order."""
    out = []
    for row in win._buff_rows:
        if isinstance(row, Gtk.Label):
            out.append(row.get_text())
            continue
        parts = [
            child.get_text()
            for child in row
            if isinstance(child, Gtk.Label)
        ]
        out.append(" ".join(parts))
    return out


def test_shop_buffs_are_rendered_under_their_own_heading(win):
    win.set_buffs((("Blood Gem", 2, 2),), (("Elemental", 27, 27),))
    assert rows(win) == [
        "Blood Gem +2/+2",
        "In Bob's Tavern",
        "Elemental +27/+27",
    ]


def test_no_heading_when_there_are_no_shop_buffs(win):
    win.set_buffs((("Blood Gem", 2, 2),), ())
    assert rows(win) == ["Blood Gem +2/+2"]


def test_shop_buffs_alone_still_show_the_panel(win):
    win.set_buffs((), (("Elemental", 1, 1),))
    assert rows(win) == ["In Bob's Tavern", "Elemental +1/+1"]
    assert win.shown["buffs"] is True


def test_a_non_elemental_shop_tribe_is_curated_out(win):
    """The panel draws only Elemental out of `shop`; every other tribe the
    reader can still recognise is deliberately not shown here."""
    win.set_buffs((), (("Beast", 3, 3),))
    assert rows(win) == []
    assert win.shown["buffs"] is False


def test_an_empty_panel_is_hidden(win):
    win.set_buffs((), ())
    assert win.shown["buffs"] is False


def test_a_repaint_does_not_stack_rows_on_the_previous_ones(win):
    win.set_buffs((), (("Elemental", 1, 1),))
    win.set_buffs((), (("Elemental", 2, 2),))
    assert rows(win) == ["In Bob's Tavern", "Elemental +2/+2"]


def test_gold_next_turn_gets_its_own_heading(win):
    win.set_buffs((), (), 2, 0)
    assert rows(win) == ["This Turn", "Gold Next Turn +2"]


def test_overdrawn_gold_next_turn_shows_the_minus_sign(win):
    win.set_buffs((), (), -1, 0)
    assert rows(win) == ["This Turn", "Gold Next Turn -1"]


def test_free_reroll_gets_its_own_heading(win):
    win.set_buffs((), (), 0, 1)
    assert rows(win) == ["This Turn", "Free Reroll 1 available"]


def test_gold_and_reroll_share_one_this_turn_heading(win):
    win.set_buffs((), (), 2, 1)
    assert rows(win) == [
        "This Turn",
        "Gold Next Turn +2",
        "Free Reroll 1 available",
    ]
    assert win.shown["buffs"] is True


def test_no_this_turn_heading_when_both_are_zero(win):
    win.set_buffs((("Blood Gem", 2, 2),), ())
    assert "This Turn" not in rows(win)
```

In `tests/test_overlay_state.py`, replace the assertion in `test_shop_buffs_reach_the_panel_alongside_the_played_buffs`:

```python
    assert win.last("set_buffs") == ((("Blood Gem", 2, 2),), (), (("Elemental", 27, 27),))
```

with:

```python
    assert win.last("set_buffs") == (
        (("Blood Gem", 2, 2),), (("Elemental", 27, 27),), 0, 0,
    )
```

Then add a new test immediately after it:

```python
def test_gold_and_rerolls_reach_the_panel():
    app, win = _app()
    app.on_event(ev.Buffs(entries=(), gold_next_turn=2, free_rerolls=1), None)
    assert win.last("set_buffs") == ((), (), 2, 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_overlay_buffs_panel.py tests/test_overlay_state.py -v`
Expected: most `test_overlay_buffs_panel.py` cases FAIL — `set_buffs` still takes `(entries, spells=(), shop=())`, so calling it with 2 or 4 positional args either raises `TypeError` or feeds values into the wrong parameters. Both `test_overlay_state.py` cases FAIL — the assertion no longer matches the old 3-tuple shape, and `gold_next_turn`/`free_rerolls` aren't unpacked by the current `case ev.Buffs` pattern.

- [ ] **Step 3: Remove `spells` from `ev.Buffs`**

In `bgtracker/parse/events.py`, replace the `Buffs` dataclass:

```python
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
```

with:

```python
@dataclass(frozen=True)
class Buffs:
    """Friendly player's curated buffs and turn economy.

    entries: (label, atk, health) board-buff counters — Blood Gem, Spell
             Power, Undead, Beetle Army — already baked into the board.
    shop:    (label, atk, health) tavern shop buffs — every tribe the reader
             recognizes, unfiltered. The panel draws only the Elemental row
             out of this; see overlay/window.py::set_buffs.
    gold_next_turn: net gold banked for next turn; negative if overdrawn.
    free_rerolls:   free rerolls available this turn.
    """

    entries: tuple[tuple[str, int, int], ...]
    shop: tuple[tuple[str, int, int], ...] = ()
    gold_next_turn: int = 0
    free_rerolls: int = 0
```

- [ ] **Step 4: Delete `read_active_spells` and `_HELD_ZONES`**

In `bgtracker/state/game.py`, delete this whole block (the comment, `_HELD_ZONES`, and `read_active_spells`):

```python
# Persistent tavern SPELLS the player holds (Easterly Winds and other pool
# spells). They aren't counters — they buff shop minions / the board on
# triggers — but are worth surfacing as active effects. Classified by the
# stable `isBattlegroundsPoolSpell` card flag, since the game morphs their
# runtime CARDTYPE (SPELL<->TRINKET) and that type is also polluted with
# cosmetic "Portraits" and the discover pool.
# SETASIDE/PLAY hold persistent tavern effects; HAND is excluded so a one-shot
# spell mid-cast doesn't flicker into the list.
_HELD_ZONES = (Zone.PLAY, Zone.SETASIDE)


def read_active_spells(game: Game, player_id: int) -> tuple[str, ...]:
    """Friendly-held tavern spells, deduped by card id."""
    pool = cards.pool_spell_ids()
    if not pool:
        return ()
    out: list[str] = []
    for e in game.entities:
        if (
            isinstance(e, Card)
            and e.card_id in pool
            and tag(e, GameTag.CONTROLLER) == player_id
            and tag(e, GameTag.ZONE) in _HELD_ZONES
            and e.card_id not in out
        ):
            out.append(e.card_id)
    return tuple(out)
```

`cards.pool_spell_ids()` is still used by `_is_trinket` elsewhere in this file — do not touch `bgtracker/data/cards.py`.

- [ ] **Step 5: Stop reading and emitting `spells` in the exporter**

In `bgtracker/parse/exporter.py`, remove `read_active_spells` from the `bgtracker.state.game` import block, leaving:

```python
from bgtracker.state.game import (
    BoardSnapshot,
    project_player_board,
    read_buffs,
    read_free_rerolls,
    read_gold_next_turn,
    read_played_buffs,
    read_shop_buffs,
    tag,
)
```

Replace `maybe_emit_buffs`'s body:

```python
    def maybe_emit_buffs(self):
        fid = self.friendly_player_id()
        if fid is None:
            return
        entries = read_buffs(self.game, fid).entries + read_played_buffs(self.game, fid)
        spells = read_active_spells(self.game, fid)
        shop = read_shop_buffs(self.game, fid)
        gold_next_turn = read_gold_next_turn(self.game, fid)
        free_rerolls = read_free_rerolls(self.game, fid)
        state = (entries, spells, shop, gold_next_turn, free_rerolls)
        if state != self._buffs:
            self._buffs = state
            self._emit(ev.Buffs(entries=entries, spells=spells, shop=shop,
                                 gold_next_turn=gold_next_turn, free_rerolls=free_rerolls))
```

with:

```python
    def maybe_emit_buffs(self):
        fid = self.friendly_player_id()
        if fid is None:
            return
        entries = read_buffs(self.game, fid).entries + read_played_buffs(self.game, fid)
        shop = read_shop_buffs(self.game, fid)
        gold_next_turn = read_gold_next_turn(self.game, fid)
        free_rerolls = read_free_rerolls(self.game, fid)
        state = (entries, shop, gold_next_turn, free_rerolls)
        if state != self._buffs:
            self._buffs = state
            self._emit(ev.Buffs(entries=entries, shop=shop,
                                 gold_next_turn=gold_next_turn, free_rerolls=free_rerolls))
```

Update the init default at `bgtracker/parse/exporter.py:92` from `((), (), (), 0, 0)` to:

```python
        self._buffs: tuple = ((), (), 0, 0)
```

- [ ] **Step 6: Update `overlay/app.py`**

Replace the case arm:

```python
            case ev.Buffs(entries=e, spells=sp, shop=shop):
                st.buffs = (e, sp, shop)
```

with:

```python
            case ev.Buffs(entries=e, shop=shop, gold_next_turn=g, free_rerolls=fr):
                st.buffs = (e, shop, g, fr)
```

Replace the reset line:

```python
        st.buffs = ((), (), ())
```

with:

```python
        st.buffs = ((), (), 0, 0)
```

- [ ] **Step 7: Update `overlay/model.py`**

Replace:

```python
    buffs: tuple[tuple, tuple, tuple] = ((), (), ())
```

with:

```python
    buffs: tuple[tuple, tuple, int, int] = ((), (), 0, 0)
```

- [ ] **Step 8: Rewrite `set_buffs` and delete `_spell_chip`**

In `bgtracker/overlay/window.py`, replace:

```python
    def set_buffs(self, entries, spells=(), shop=()) -> None:
        """Played buffs and held spells, then tavern-wide buffs under a heading.

        `shop` is a different quantity from `entries`: those are already on the
        board, these are what a minion in Bob's tavern is carrying before you
        buy it. In one flat list the two read as a single stacking number.
        """
        for row in self._buff_rows:
            self.buffs.remove(row)
        self._buff_rows.clear()
        for label, atk, hp in entries:
            self._buff_rows.append(self._buff_chip(label, f"+{atk}/+{hp}"))
        for card_id in spells:
            self._buff_rows.append(self._spell_chip(cards.name(card_id)))
        if shop:
            self._buff_rows.append(self._buff_group("In Bob's Tavern"))
            for label, atk, hp in shop:
                self._buff_rows.append(self._buff_chip(label, f"+{atk}/+{hp}"))
        for row in self._buff_rows:
            self.buffs.append(row)
        self._set_content("buffs", bool(self._buff_rows))
```

with:

```python
    def set_buffs(self, entries, shop=(), gold_next_turn=0, free_rerolls=0) -> None:
        """Board buffs, then the Elemental tavern buff, then turn economy.

        `shop` arrives carrying every tribe the reader recognises; the panel
        only ever draws the Elemental row out of it, because a minion sitting
        in Bob's tavern is a different quantity from one already on your
        board — flattened into one list the two read as a single stacking
        number.
        """
        for row in self._buff_rows:
            self.buffs.remove(row)
        self._buff_rows.clear()
        for label, atk, hp in entries:
            self._buff_rows.append(self._buff_chip(label, f"+{atk}/+{hp}"))
        shop = tuple(b for b in shop if b[0] == "Elemental")
        if shop:
            self._buff_rows.append(self._buff_group("In Bob's Tavern"))
            for label, atk, hp in shop:
                self._buff_rows.append(self._buff_chip(label, f"+{atk}/+{hp}"))
        if gold_next_turn or free_rerolls:
            self._buff_rows.append(self._buff_group("This Turn"))
            if gold_next_turn:
                sign = "+" if gold_next_turn > 0 else ""
                self._buff_rows.append(
                    self._buff_chip("Gold Next Turn", f"{sign}{gold_next_turn}")
                )
            if free_rerolls:
                self._buff_rows.append(
                    self._buff_chip("Free Reroll", f"{free_rerolls} available")
                )
        for row in self._buff_rows:
            self.buffs.append(row)
        self._set_content("buffs", bool(self._buff_rows))
```

Delete the `_spell_chip` method entirely:

```python
    def _spell_chip(self, name: str) -> Gtk.Widget:
        label = Gtk.Label(label=f"✦ {name}", xalign=0)
        label.add_css_class("spell")
        label.set_wrap(True)
        return label
```

Delete the now-unused import at the top of the file:

```python
from bgtracker.data import cards  # noqa: E402
```

(`cards.name(card_id)` was the only use of this import in `window.py` — confirm with `grep -n "cards\." bgtracker/overlay/window.py` before deleting; it should show nothing.)

- [ ] **Step 9: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_overlay_buffs_panel.py tests/test_overlay_state.py -v`
Expected: all PASS.

- [ ] **Step 10: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add bgtracker/parse/events.py bgtracker/state/game.py bgtracker/parse/exporter.py \
        bgtracker/overlay/app.py bgtracker/overlay/model.py bgtracker/overlay/window.py \
        tests/test_overlay_buffs_panel.py tests/test_overlay_state.py
git commit -m "$(cat <<'EOF'
Retire held tavern spells; surface the curated buffs and turn economy

Held tavern spells (Easterly Winds and friends) had no reader-level
consumer besides the panel and are dropped outright per the curation
spec - read_active_spells and _HELD_ZONES are deleted, not deprecated.

set_buffs now takes (entries, shop, gold_next_turn, free_rerolls). The
Elemental-only curation of `shop` happens here, at the last mile, so
ev.Buffs.shop stays the full multi-tribe readout test_shop_buffs.py
already tests. gold_next_turn and free_rerolls, added to the event two
commits ago, finally reach the screen under a new "This Turn" heading.

This is one commit across events/exporter/app/model/window because
app.py's case pattern, the positional tuple it builds, and window.py's
set_buffs signature only make sense read together - splitting them
would leave a commit where the match silently stops firing.
EOF
)"
```

---

## Task 7: Theme the new and renamed buff rows

**Files:**
- Modify: `bgtracker/overlay/theme.py` (`BUFF_COLOURS`, remove `SPELL`/`.spell`)
- Modify: `tests/test_overlay_theme.py`

**Interfaces:**
- Consumes: `_PLAYED_SCRIPT_BUFFS` (Task 3), `_BUFF_TAGS` (Task 1), `_SHOP_BUFFS` (unchanged).

- [ ] **Step 1: Write the failing test**

In `tests/test_overlay_theme.py`, replace `test_every_buff_label_the_reader_can_emit_has_a_colour`:

```python
def test_every_buff_label_the_reader_can_emit_has_a_colour():
    """The other direction: a label with no dot colour ships an invisible dot.

    Tribes rotate, so the shop-buff table gains entries over time; without this
    a new one looks fine in code review and blank on screen.
    """
    from bgtracker.state.game import _BUFF_TAGS, _SHOP_BUFFS

    labels = {label for label, *_ in _BUFF_TAGS} | set(_SHOP_BUFFS.values())
    assert labels <= set(theme.BUFF_COLOURS)
```

with:

```python
def test_every_buff_label_the_reader_can_emit_has_a_colour():
    """The other direction: a label with no dot colour ships an invisible dot.

    Tribes rotate, so the shop-buff table gains entries over time; without this
    a new one looks fine in code review and blank on screen.
    """
    from bgtracker.state.game import _BUFF_TAGS, _PLAYED_SCRIPT_BUFFS, _SHOP_BUFFS

    labels = (
        {label for label, *_ in _BUFF_TAGS}
        | set(_SHOP_BUFFS.values())
        | set(_PLAYED_SCRIPT_BUFFS.values())
    )
    assert labels <= set(theme.BUFF_COLOURS)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_overlay_theme.py -v`
Expected: `test_every_buff_label_the_reader_can_emit_has_a_colour` FAILS — `_BUFF_TAGS` now yields `"Spell Power"`, which has no `BUFF_COLOURS` key yet (only the old `"Spell"` does), and `_PLAYED_SCRIPT_BUFFS` yields `"Beetle Army"`, which has no key at all.

- [ ] **Step 3: Update `BUFF_COLOURS`**

In `bgtracker/overlay/theme.py`, replace the comment and dict:

```python
# Tavern-buff dot colours, keyed by the labels in state.game._BUFF_TAGS and
# _SHOP_BUFFS. The design handoff defines no tribe palette — its only
# tribe-adjacent colours are the keyword pips — so the shop-buff tribes below
# extend these four in the same muted jewel-tone register rather than opening a
# second palette. Elemental and Pirate are shared by both tables.
BUFF_COLOURS = {
    "Blood Gem": "#d0555f",
    "Elemental": "#3fb0c9",
    "Pirate": "#c9a13f",
    "Spell": "#a97fd0",
    "Beast": "#8fae54",
    "Demon": "#b45fa8",
    "Dragon": "#d1743f",
    "Mech": "#7f96b0",
    "Murloc": "#4fbf9a",
    "Naga": "#6f7fd0",
    "Quilboar": "#c96f86",
    "Undead": "#b9b3a0",
    "All minions": "#e0cfa4",
    "Multi-tribe": "#a9b4c0",
}
```

with:

```python
# Tavern-buff dot colours, keyed by the labels in state.game._BUFF_TAGS,
# _SHOP_BUFFS, and _PLAYED_SCRIPT_BUFFS, plus the two turn-economy rows that
# have no reader table at all (their labels are literals in window.py's
# set_buffs). The design handoff defines no tribe palette — its only
# tribe-adjacent colours are the keyword pips — so these extend the original
# four buff dots in the same muted jewel-tone register rather than opening a
# second palette. Elemental and Pirate are shared by more than one table.
# "Undead" and "Pirate" each cover two different underlying readers (a played
# buff and a tavern-shop buff) that happen to share a label and a colour.
BUFF_COLOURS = {
    "Blood Gem": "#d0555f",
    "Elemental": "#3fb0c9",
    "Pirate": "#c9a13f",
    "Spell Power": "#a97fd0",
    "Beast": "#8fae54",
    "Demon": "#b45fa8",
    "Dragon": "#d1743f",
    "Mech": "#7f96b0",
    "Murloc": "#4fbf9a",
    "Naga": "#6f7fd0",
    "Quilboar": "#c96f86",
    "Undead": "#b9b3a0",
    "All minions": "#e0cfa4",
    "Multi-tribe": "#a9b4c0",
    "Beetle Army": "#b07a3f",
    "Gold Next Turn": "#f4d47a",
    "Free Reroll": "#5fa3d0",
}
```

- [ ] **Step 4: Remove the unused `.spell` styling**

In `bgtracker/overlay/theme.py`, delete the `SPELL` constant:

```python
SPELL = "#8fd0e6"
```

Delete the `.spell` CSS rule:

```python
.spell { color: $spell; font-weight: 600; font-size: ${f12_5}px; }
```

In the `stylesheet()` function's `_CSS.substitute(...)` call, remove `spell=SPELL` from:

```python
        win=WIN, tie=TIE, loss=LOSS, spell=SPELL,
```

leaving:

```python
        win=WIN, tie=TIE, loss=LOSS,
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_overlay_theme.py -v`
Expected: all PASS, including `test_stylesheet_parses_at_the_auto_scale_for_common_monitors` (confirms the template still substitutes cleanly with `spell` removed) and `test_every_pip_and_buff_colour_reaches_the_stylesheet` (confirms the three new/renamed keys each got a generated `.buff-dot.buff-*` rule).

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add bgtracker/overlay/theme.py tests/test_overlay_theme.py
git commit -m "$(cat <<'EOF'
Theme the curated buffs and turn-economy rows

"Spell" -> "Spell Power" (same hex, key renamed to match the reader).
"Pirate" stays even though _BUFF_TAGS dropped its played-buff entry -
_SHOP_BUFFS still maps BG_ShopBuff_Pirate to that label, and the
theme-coverage test checks the reader's full emission surface, not
what the panel displays. New: Beetle Army, Gold Next Turn (reuses the
$gold token's hex so the dot ties to the currency identity used
elsewhere in the overlay), Free Reroll. Removed the now-unused .spell
rule and its SPELL constant along with it.
EOF
)"
```

---

## Task 8: Update the demo script and verify end to end

**Files:**
- Modify: `bgtracker/overlay/demo.py`

**Interfaces:**
- Consumes: the final `ev.Buffs` shape from Task 6.

`overlay/demo.py` has no automated test coverage (confirmed: no test file references it). Its verification is the manual `--overlay --demo` check the spec itself calls for.

- [ ] **Step 1: Update the seeded `Buffs` event**

In `bgtracker/overlay/demo.py`, replace:

```python
    buffs = ev.Buffs(entries=(("Blood Gem", 2, 2), ("Elemental", 4, 3)),
                     spells=("BG28_800", "BG28_168"),  # Careful Investment, Shiny Ring
                     # A Nomi board: the tavern-wide group, big enough to show
                     # the two-digit layout the real thing reaches by turn 9.
                     shop=(("All minions", 1, 1), ("Elemental", 27, 27)))
```

with:

```python
    buffs = ev.Buffs(
        entries=(
            ("Blood Gem", 2, 2),
            ("Spell Power", 1, 1),
            ("Undead", 4, 0),
            ("Beetle Army", 12, 8),
        ),
        # A Nomi board, big enough to show the two-digit layout the real thing
        # reaches by turn 9. "All minions" is here on purpose — it proves the
        # panel curates shop buffs down to just Elemental even with a second
        # tribe active.
        shop=(("All minions", 1, 1), ("Elemental", 27, 27)),
        gold_next_turn=2,
        free_rerolls=1,
    )
```

- [ ] **Step 2: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: all PASS.

- [ ] **Step 3: Verify visually**

Run: `.venv/bin/python -m bgtracker --overlay --demo`

Confirm in the Buffs panel, during the recruit-phase portion of the loop:
- Four board-buff rows: Blood Gem +2/+2, Spell Power +1/+1, Undead +4/+0, Beetle Army +12/+8 — each with a distinct dot colour.
- An "In Bob's Tavern" heading followed by exactly one row, Elemental +27/+27 — "All minions" must NOT appear.
- A "This Turn" heading followed by Gold Next Turn +2 and Free Reroll 1 available.
- No leftover spell chips (✦ Careful Investment / Shiny Ring) anywhere.

If the Beetle Army or Free Reroll dot colour reads too close to a neighboring dot, adjust the hex in `theme.BUFF_COLOURS` (`bgtracker/overlay/theme.py`) and re-run this step — the spec left these as starting points for exactly this check.

- [ ] **Step 4: Commit**

```bash
git add bgtracker/overlay/demo.py
git commit -m "$(cat <<'EOF'
Exercise the curated buffs panel in --overlay --demo

Seeds Undead, Beetle Army, gold-next-turn and free-reroll sample
values, and deliberately includes a non-Elemental shop buff ("All
minions") so the demo proves the panel's curation rather than just
its happy path.
EOF
)"
```
