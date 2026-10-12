# A panel showing the lobby's tribes

## Problem

The overlay never says which five tribes are in the lobby. The game shows them
on the hero-select screen and then hides them behind a hover for the rest of
the match, and that is the moment a player planning a build wants them. The
request: a small panel with the tribe icons, no text beyond a title.

## What the log gives

`2026-09-10-lobby-tribes-design.md` established that **no entity states the
lobby's tribes**. Re-checked against `hearthstone` 9.21.1: the only candidate
tags are still the per-minion `BACON_SUBSET_*` flags, which out-of-lobby
tokens and hero-select art also carry. HDT and Firestone read the list from
game memory, which this tracker does not do.

What that spec did not test is **Bob's offers specifically**. Every minion
Bob puts in the tavern is drawn from the lobby's pool, so a single-tribe
offer is evidence that the tribe is in the lobby, not merely a count.

Evidence: every session log from 2026-10-02 to 2026-10-10 (six sessions,
~1.1 GB, nine games), replayed through `LiveGameProcessor`. Each minion was
counted once per entity, and only if it was in `Zone.PLAY` under a controller
other than ours while `BOARD_VISUAL_STATE` read shop. Its tribes were taken
from the card DB's `races`.

| Game | Tribes in first-seen order (turn) |
|---|---|
| 10-02 #1 | Dragon 1, Quilboar 2, Elemental 5, Murloc 7, Demon 8 |
| 10-05 #1 | Aberration 1, Beast 1, Mech 1, Elemental 3, Undead 8 |
| 10-08 #1 | Mech 1, Quilboar 1, Undead 1, Elemental 1, Pirate 2 |
| 10-09a #1 | Beast 1, Mech 1, Pirate 1, Dragon 3, Elemental 8 |
| 10-09a #2 | Quilboar 1, Undead 1, Pirate 2, Elemental 3, Aberration 4 |
| 10-09a #3 | Beast 1, Quilboar 1, Murloc 1, Aberration 2, Mech 4 |
| 10-09b #1 | Pirate 1, Undead 1, Dragon 1, Murloc 2, Elemental 3 |
| 10-10 #1 | Pirate 1, Quilboar 1, Aberration 1, Beast 1, Elemental 2 |
| 10-10 #2 | Aberration 1, Mech 1, Dragon 1, Pirate 2, Quilboar 4 |

**Every game read exactly five tribes, and none ever read a sixth.** Nothing
in this corpus put an out-of-lobby single-tribe minion in Bob's tavern.

Two kinds of offer are excluded, and both exclusions matter:

- **Multi-tribe minions** (Demon/Naga, Undead/Mech, Cyborg Drake's
  Mech/Dragon, …) appeared in every game. Whether a dual-tribe minion needs
  *both* tribes in the lobby or only one is not stated anywhere, so one
  proves nothing about either tribe. `ALL` minions (Amalgadon) likewise.
- **`IS_BACON_POOL_MINION = 0`** offers. The corpus shows `BG36_360t*`
  (an `ALL`-tribe token) and once `BG36_205`, which effects put in the shop
  directly. They are not pool draws, so they are not evidence. The one Beast
  among them was in a Beast lobby anyway, which is luck, not a rule.

## Decision

Show only **confirmed** tribes: those seen at least once as a single-tribe
pool minion in Bob's tavern this game. The panel starts empty and fills in
as tribes are confirmed. Usually that is complete by turn 2–4, but three of
the nine games took until turn 7–8.

That is deliberately not what the 2026-09-10 spec asks of `validTribes`.
There a partial list is a *wrong* simulator input: it would exclude a real
tribe from the summon pool. Here a partial list is an honest display, since
every icon shown is a tribe that is in the lobby, and a missing one has
simply not been offered yet. The panel never shows a guess, and nothing here
is sent to the simulator. The read is not judged against an oracle (no
hero-select tribes were noted down), so it does not meet that spec's bar for
`validTribes`, and is not used for it.

No placeholder slots for unconfirmed tribes: drawing five slots would assert a
lobby size the log does not state.

## Shape

- **Parse.** `BGExporter` checks the one entity a packet touches: a
  `FULL_ENTITY`, a `SHOW_ENTITY` (where the card id is revealed), and
  `TAG_CHANGE`s of `ZONE` or `CONTROLLER`. That is O(1) per packet, never a
  walk of the entity tree. A new tribe emits `LobbyTribes(tribes=…)`, sorted.
  The exporter is per game, so the set resets with it.
- **Card data.** Tribes come from the card DB (`data/cards.py::tribes`), the
  same source the evidence used. With `--no-names` there is no DB and the
  panel stays empty, which fails closed.
- **Overlay.** `OverlayState.tribes`, rendered by `window.set_tribes`. It is
  a movable panel titled "Tribes" holding one round icon per tribe. It is
  hidden while empty and cleared at `GameStart`. Tooltips would be pointless,
  since the overlay never takes the pointer.
- **Icons.** These are Blizzard's own tribe art tiles, the ones HDT ships in
  `Resources/TribeIcons/<tribe>.jpg` (256×256, all eleven tribes including
  Aberration). They are fetched once into `~/.cache/hs-bg-tracker/art/tribes/`
  through the same `data/art.py` path as card art: async, magic-byte checked,
  and a time-bounded retry on a miss. They are not committed to the repo,
  for the same copyright reason the card art is not. Until an icon arrives,
  the round backdrop shows on its own.

## Not done

- Using the read for the simulator's `validTribes`. That still needs the
  oracle the 2026-09-10 spec describes.
- Banned-tribe display.
- The Aberration deity indicator HDT draws on the Aberration icon.
