# Quests and quest rewards: stop treating a quest as a secret, start reading the reward

## Problem

Two failures, one cause: the tracker has no notion of a quest.

**A quest in progress blocks the forecast.** `resim 800` on 2026-11-17 failed 18 fights
as `combat behavior unsupported`, 17 of them on a quest id:

| Card | Text | Failures |
|---|---|---|
| `BG24_Quest_313` Invite the Guests | Quest: Buy N minions. | 9 |
| `BG24_Quest_151` Unlikely Duo | Quest: Buy N \<tribe\> or \<tribe\>. | 7 |
| `BG24_Quest_126` Follow the Money | Quest: Spend N Gold. | 1 |

None of these can do anything in a fight. They were refused because an accepted quest
waits in `Zone.SECRET`, `project_player_board` files everything in that zone under
`PlayerBoard.secrets`, and the mapper marks every secret as combat behaviour "by
definition". The sidecar then looks for the id in the simulator package, finds no code
for an objective that never acts, and fails closed. Over the whole history that is 45
quest-holding boards with no prediction.

Quests the package *does* mention (`BG24_Quest_114/123/124/314/352`, `BG27_Quest_801/802`)
passed the check and were sent — as a `BoardSecret`, a shape the simulator ignores for
them. So those fights got odds, computed without the quest.

**A completed reward is never read at all.** When a quest completes, the quest entity
leaves and a separate reward entity appears in `PLAY`. Nothing projects it. Every fight
after completion is simulated without the reward, with no error and no mark on the
number. Some rewards are large: Staff of Origination is `Start of Combat: Give your
minions +12/+12`.

This is not a seasonal mode. Quests come from Sire Denathrius's hero power (`Whodunit?`,
`BG24_HERO_100p`), so they appear in ordinary lobbies whenever he is picked. The first
stored row is from 2026-08-07.

## Log evidence

Session `Hearthstone_2026_11_14_15_15_36`, a game played as Denathrius.

The quest is offered as a discover, then moves to the secret zone when chosen:

```
FULL_ENTITY - Creating ID=447 CardID=BG27_Quest_802
    tag=CONTROLLER value=1
    tag=CARDTYPE value=SPELL
    tag=ZONE value=SETASIDE
    tag=QUEST value=1
    tag=QUEST_PROGRESS_TOTAL value=17
TAG_CHANGE Entity=447 tag=QUEST_REWARD_DATABASE_ID value=104673
TAG_CHANGE Entity=447 tag=QUEST_PROGRESS_TOTAL value=30
...
TAG_CHANGE Entity=[... id=447 zone=SETASIDE ...] tag=ZONE value=SECRET
TAG_CHANGE Entity=[... id=447 zone=SECRET ...] tag=QUEST_PROGRESS value=1
```

`CARDTYPE` is `SPELL`, the same as a real combat secret, so the type cannot separate
them. `QUEST = 1` can. The declined offer (`BG24_Quest_151`, id 445) goes to
`REMOVEDFROMGAME` and has its `QUEST` tag cleared.

On completion the quest leaves and the reward is created directly in play:

```
TAG_CHANGE Entity=[... id=447 zone=SECRET ...] tag=ZONE value=SETASIDE
TAG_CHANGE Entity=[Boss Denathrius id=119 ...] tag=BACON_QUEST_COMPLETED value=1
FULL_ENTITY - Creating ID=6745 CardID=BG27_Reward_802
    tag=CONTROLLER value=1
    tag=CARDTYPE value=BATTLEGROUND_QUEST_REWARD
    tag=TAG_SCRIPT_DATA_NUM_1 value=120754
    tag=ZONE value=PLAY
    tag=CREATOR value=447
TAG_CHANGE Entity=[Whodunit? id=153 ...] tag=ZONE value=GRAVEYARD
TAG_CHANGE Entity=[... id=447 ...] tag=ZONE value=REMOVEDFROMGAME
```

The hero power goes to the graveyard at the same moment, which is why a completed
Denathrius snapshots with `hero_power_id = None`. A reward that was offered and not taken
(`BG24_Reward_719`, id 446) is created in `SETASIDE` and stays there, so as with trinkets
the **zone** is what says a reward is equipped.

The opponent's quest and reward are both materialized for the fight, under the shared
controller 14, exactly like their secrets and trinkets. An opponent's quest was seen
advancing *during* combat in GameState (`QUEST_PROGRESS` 12 → 15 on "Have N friendly
minions die"), which confirms the game does count combat events toward a quest.

Replaying all five retained session logs through the new projection: 337 combats, 23
with a quest or reward on one side, every one read with its reward dbfId, progress and
total (`BG27_Quest_802` 0/30 on turn 1 rising to 21/30 on turn 8, replaced by
`BG27_Reward_802` from turn 9).

## What the simulator wants

`BgsPlayerEntity` (package 1.1.780) has three fields:

| Field | Shape | Read by |
|---|---|---|
| `questEntities` | `{CardId, RewardDbfId, ProgressCurrent, ProgressTotal}` | per-event `switch (quest.CardId)` in the death, attack, summon, stat and hand handlers |
| `questRewards` | `string[]` of card ids | the start-of-combat pass and every `questRewards.includes(...)` |
| `questRewardEntities` | `{CardId, ScriptDataNum1}` on input | setup, which rebuilds them with avenge counters |

The PascalCase is the package's own: `setupHero` reads `reward.CardId` and
`reward.ScriptDataNum1` verbatim, and `quest.CardId` in every switch. Sending `cardId`
would be accepted and ignored.

Eight quests advance in combat in the package: `BG24_Quest_114` (summon), `_123` (stat
increase), `_124` (friendly deaths), `_314` (cards to hand), `_320` (deathrattles),
`_352` (attacks), `BG27_Quest_801` (total attack) and `_802` (enemy deaths). On reaching
the total it looks the reward up by `RewardDbfId` and grants it mid-fight. That is real
modelling the tracker was withholding by sending `questEntities: []`.

## Design

**Snapshot.** Two new dataclasses in `state/game.py`, `Quest` and `QuestReward`, and two
new `PlayerBoard` fields. In `Zone.SECRET`, an entity with `QUEST = 1` is a quest;
anything else stays a secret. In `Zone.PLAY`, `CARDTYPE = BATTLEGROUND_QUEST_REWARD` is a
reward. `cards.is_quest()` (the card DB's `QUEST` mechanic, or the `_Quest_` id shape)
backs the tag up.

**Mapper.** Quests go out as `questEntities`, rewards as both `questRewards` and
`questRewardEntities`. What the sidecar is asked to prove:

- A **reward** is judged like a trinket: marked only if its text acts in combat
  (`_combat_relevant`). Most are economy.
- A **quest** is marked only if its objective can advance in combat
  (`_COMBAT_QUEST_TEXT`: summon, die, attack, destroy, deathrattle, stats, to your hand,
  cast). A quest that only moves in the tavern is sent and asks nothing.
- A quest whose card the DB does not know is treated as the dangerous kind.

Against today's card DB that marks the eight the package implements, plus
`BG24_Quest_318` (taunt minion attacked), `BG24_Quest_125` (order by attack for N
combats) and `BG28_Quest_500` (cast spells), which it does not — so those three fail
closed. The first is correct. The other two are guesses that err toward no number:
no captured fight shows whether they tick during combat.

**Stored rows.** Rows written before this change hold quests inside `secrets`, with the
id and no counters. `board_from_json` refiles them as `Quest(total=None)`. The mapper
then omits a tavern quest silently, and blocks a combat-advancing one as
`quest state not recorded` — the same rule Wax Warband's unrecorded `NUM_3` follows.
No migration: the rows are a projection of a log that is mostly gone, and the refile
happens on read.

**Sidecar.** `compatibility.mjs` walks `questEntities` and `questRewardEntities` too, and
reads `CardId` as well as `cardId`. Without that the hint would name a quest the entity
walk never visits, and an unimplemented one would pass.

## What this does not fix

- **Rewards before today are unrecoverable.** Old rows after a quest completed carry no
  reward, and nothing in the row says one existed. Their stored predictions and any
  replay of them are both without it.
- **A reward granted mid-combat is not checked.** If a combat-advancing quest completes
  during the fight, the package grants `RewardDbfId`. Nothing verifies the package
  implements *that* reward, because no input entity carries its id. Every combat-acting
  reward in the current card DB is implemented, so this is a gap in the guard, not a
  known wrong number.
- **Haunted Carapace** (`BG33_112t`) is the one remaining `unsupported` in the corpus. It
  also sits in the secret zone but is not a quest. The package wants it as
  `globalInfo.HauntedCarapaceAttack/HealthBonus`; where the log keeps those values has
  not been looked at. One fight in 800.

## Verification

- Replay of five session logs: 23 quest or reward boards, all read, none blocked.
- Those 23 fights simulated with and without the new fields: all return odds. The
  differences are small on this sample (largest 1.5 points) because the rewards in it
  are economy ones; the roundtrip test below is what proves the wiring.
- `test_sim_roundtrip.py`: a 2/3 that loses to a 1/7 taunt 100% of the time wins 100%
  with Staff of Origination in `questRewards`.
- `resim 800`, by Brier direction — recorded in the implementation commit.
