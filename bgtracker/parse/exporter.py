"""Live, incremental hslog parsing with BG event detection.

hslog's EntityTreeExporter is built for whole-game export; here we export
top-level packets as they complete (a still-open trailing Block is deferred
until its BLOCK_END arrives) so the entity tree stays live during play.

BG phase detection rides on GameTag.BOARD_VISUAL_STATE on the GAME entity:
1 = shop/recruit phase, 2 = combat. Both boards are fully materialized in
the entity tree at the 1->2 transition, which is when we snapshot.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field

from hearthstone.entities import Card, Game
from hearthstone.enums import CardType, GameTag, State, Zone
from hslog import LogParser
from hslog import packets as hspackets
from hslog.export import EntityTreeExporter

from bgtracker.parse import events as ev
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

log = logging.getLogger(__name__)

BOB_HERO_ID = "TB_BaconShopBob"

SHOP = 1
COMBAT = 2

# Hero tags that move a player's leaderboard display.
_HERO_HP_TAGS = frozenset({GameTag.HEALTH, GameTag.DAMAGE, GameTag.ARMOR})
_HERO_STANDING_TAGS = _HERO_HP_TAGS | {GameTag.ZONE, GameTag.PLAYER_TECH_LEVEL}

# Tags that can change what the friendly board looks like during the shop —
# buying, selling, repositioning, and every buff that lands on a minion.
# Projecting the board walks the whole entity tree, which is far too expensive
# to do per packet, so this narrows it to packets that could plausibly matter
# and the projection then confirms whether anything really changed.
_SHOP_BOARD_TAGS = frozenset({
    GameTag.ZONE, GameTag.ZONE_POSITION, GameTag.ATK, GameTag.HEALTH,
    GameTag.DAMAGE, GameTag.ATTACHED, GameTag.TAUNT, GameTag.DIVINE_SHIELD,
    GameTag.POISONOUS, GameTag.VENOMOUS, GameTag.WINDFURY, GameTag.MEGA_WINDFURY,
    GameTag.REBORN, GameTag.STEALTH, GameTag.PREMIUM, GameTag.TECH_LEVEL,
})

# Power.log carries two streams. GameState is the authoritative game state and
# is what hslog parses; it flips out of combat about a second after flipping
# in, because that is how long the engine takes to resolve the fight.
# PowerTaskList is the client's animation queue — its flip back to shop is when
# the player actually stops watching the battle (median 23s later, up to 48s).
# Nothing the player looks at should be driven by the GameState timing.
_ANIMATION_SHOP = re.compile(
    r"PowerTaskList\.DebugPrintPower\(\).*"
    r"TAG_CHANGE Entity=GameEntity tag=BOARD_VISUAL_STATE value=1"
)


def _tavern_tier(hero: Card) -> int | None:
    """Observed Battlegrounds tavern tier, or unknown when the tag is absent.

    A missing tag is not evidence for tier 1 during mid-session catch-up, and
    accepting an out-of-range value would put a plausible-looking lie on all
    eight leaderboard rows.
    """
    tier = tag(hero, GameTag.PLAYER_TECH_LEVEL, None)
    return tier if isinstance(tier, int) and 1 <= tier <= 6 else None


class BGExporter(EntityTreeExporter):
    """EntityTreeExporter that emits BG events as tag changes stream in."""

    def __init__(self, packet_tree, emit, player_manager=None):
        super().__init__(packet_tree, player_manager=player_manager)
        self._emit = emit
        self._board_state = SHOP
        self._turn = 0
        self._friendly_id: int | None = None
        self._hero_emitted: str | None = None
        self._pending_combat = False
        self._ended = False
        self._end_emitted = False
        self._standings_dirty = False
        self._standings: tuple = ()
        # Elimination is permanent, but a Kel'Thuzad ghost fight reuses the
        # dead player's hero entity and can reset its HP — latch deaths here.
        self._dead_player_ids: set[int] = set()
        self._shop_dirty = False
        self._shop_board = None
        # (entries, shop, gold, rerolls) — seeded empty so the first read of an
        # unbuffed player doesn't look like a change and emit a no-op Buffs
        # event.
        self._buffs: tuple = ((), (), 0, 0)

    # -- friendly player detection -------------------------------------
    # Only the local player's cards are revealed in their HAND zone (hero
    # picks pass through it at game start), so the first controller with a
    # revealed hand card is us.
    def friendly_player_id(self) -> int | None:
        if self._friendly_id is None:
            for entity in self.game.entities:
                if (
                    isinstance(entity, Card)
                    and entity.card_id
                    and tag(entity, GameTag.ZONE) == Zone.HAND
                ):
                    self._friendly_id = tag(entity, GameTag.CONTROLLER) or None
                    break
        return self._friendly_id

    def _heroes_in_play(self) -> list[Card]:
        return [
            e
            for e in self.game.entities
            if isinstance(e, Card)
            and e.type == CardType.HERO
            and tag(e, GameTag.ZONE) == Zone.PLAY
            and not e.card_id.startswith(BOB_HERO_ID)  # skins: TB_BaconShopBob_SKIN_*
        ]

    def friendly_hero(self) -> Card | None:
        fid = self.friendly_player_id()
        for hero in self._heroes_in_play():
            if tag(hero, GameTag.CONTROLLER) == fid:
                return hero
        return None

    def snapshot(self) -> BoardSnapshot:
        fid = self.friendly_player_id()
        opp_id = None
        for hero in self._heroes_in_play():
            controller = tag(hero, GameTag.CONTROLLER)
            if controller and controller != fid:
                opp_id = controller
                break
        return BoardSnapshot(
            turn=self._turn,
            friendly=project_player_board(self.game, fid) if fid else None,
            opponent=project_player_board(self.game, opp_id) if opp_id else None,
            damage_cap=tag(self.game, GameTag.BACON_COMBAT_DAMAGE_CAP, 0),
        )

    def maybe_emit_combat(self):
        if not self._pending_combat:
            return
        snap = self.snapshot()
        if snap.opponent is not None:
            self._pending_combat = False
            self._emit(ev.CombatStart(snapshot=snap))

    def maybe_emit_shop_board(self):
        """Friendly board during the shop, so odds can follow what you build.

        The dirty flag narrows the work to packets that could have changed the
        board; the projection then decides whether anything actually did. Both
        gates matter — the flag keeps this off the per-packet hot path, and the
        comparison keeps a redundant tag write from triggering a re-simulation.
        """
        if not self._shop_dirty or self._board_state != SHOP:
            return
        self._shop_dirty = False
        fid = self.friendly_player_id()
        if fid is None:
            return
        board = project_player_board(self.game, fid)
        if board is not None and board != self._shop_board:
            self._shop_board = board
            self._emit(ev.ShopBoard(board=board, turn=self._turn))

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

    def maybe_emit_hero(self):
        # A BG player picks one real hero per game.  Once it is known, scanning
        # every entity after every later packet can never change the event.
        if self._hero_emitted is not None:
            return
        hero = self.friendly_hero()
        if hero is None or not hero.card_id:
            return
        # Placeholder hero entities (e.g. BaconPHhero) sit in play until the
        # pick resolves; wait for the real one.
        if "PH" in hero.card_id:
            return
        self._hero_emitted = hero.card_id
        self._emit(ev.HeroPicked(card_id=hero.card_id))

    # -- tag-change hooks ----------------------------------------------
    def handle_tag_change(self, packet):
        entity = super().handle_tag_change(packet)
        gametag, value = packet.tag, packet.value

        if isinstance(entity, Game):
            if gametag == GameTag.TURN:
                # BG increments the internal counter for every recruit AND
                # combat phase; the turn shown in-game is (raw + 1) // 2.
                bg_turn = (value + 1) // 2
                if bg_turn != self._turn:
                    self._turn = bg_turn
                    self._emit(ev.TurnChange(turn=bg_turn))
            elif gametag == GameTag.BOARD_VISUAL_STATE and value != self._board_state:
                self._board_state = value
                if value == COMBAT:
                    # The opponent's board may materialize a few packets after
                    # the flag flips; emission is deferred until it exists.
                    self._pending_combat = True
                    self._shop_board = None   # next shop phase re-reports fresh
                    self.maybe_emit_combat()
                elif value == SHOP:
                    self._pending_combat = False
                    self._shop_dirty = True
                    self._emit(ev.CombatEnd(
                        snapshot=self.snapshot(),
                        eliminated=frozenset(self._dead_player_ids),
                    ))
            elif gametag == GameTag.STATE and value == State.COMPLETE and not self._ended:
                # Placement tags land a few packets AFTER the COMPLETE state;
                # defer GameEnd until we see one (or give up on next game).
                self._ended = True
                self._maybe_emit_end()
        elif gametag == GameTag.NEXT_OPPONENT_PLAYER_ID and value:
            self._emit(ev.NextOpponent(player_id=value))
        elif gametag == GameTag.PLAYER_LEADERBOARD_PLACE:
            self._standings_dirty = True
            if self._ended:
                self._maybe_emit_end()
        elif gametag in _HERO_STANDING_TAGS and getattr(entity, "type", None) == CardType.HERO:
            # The rail shows live HP, which changes far more often than a
            # player's place. maybe_emit_standings() dedupes, so flagging on
            # every hero HP tick costs nothing but keeps the numbers current.
            self._standings_dirty = True
            # Projection is coalesced to the end of the input batch, but death
            # is an observed transition rather than just final display state.
            # A Kel'Thuzad ghost can restore this entity's HP later in the same
            # batch, so latch it at the packet where it becomes visible.
            #
            # Except off a combat hero copy being cleaned up: the game moves it
            # to REMOVEDFROMGAME, zeroes HEALTH and restores it in one burst,
            # and read at the packet that zero is every opponent "dying" after
            # each fight. A real lethal lands on the copy while it is in PLAY
            # and sends it to GRAVEYARD, so it still latches here — which is
            # what lets CombatEnd see an opponent killed in this fight.
            player_id = tag(entity, GameTag.PLAYER_ID)
            health = tag(entity, GameTag.HEALTH) - tag(entity, GameTag.DAMAGE)
            zone = tag(entity, GameTag.ZONE)
            if player_id and zone != Zone.REMOVEDFROMGAME and (
                health <= 0 or zone == Zone.GRAVEYARD
            ):
                self._dead_player_ids.add(player_id)

        # Deliberately outside the chain above: HEALTH already belongs to the
        # hero branch, and a minion buff must still flag the board as stale.
        if self._board_state == SHOP and gametag in _SHOP_BOARD_TAGS:
            self._shop_dirty = True
        return entity

    def maybe_emit_standings(self):
        if not self._standings_dirty:
            return
        self._standings_dirty = False
        friendly = self.friendly_player_id()
        places: dict[int, ev.Standing] = {}
        for entity in self.game.entities:
            if not isinstance(entity, Card) or entity.type != CardType.HERO:
                continue
            if not entity.card_id or "PH" in entity.card_id or entity.card_id.startswith(BOB_HERO_ID):
                continue
            place = tag(entity, GameTag.PLAYER_LEADERBOARD_PLACE)
            if place:
                # PLAYER_ID is the stable per-player identity; the controller
                # is a shared slot. Newest entity wins (ghost copies linger) —
                # except that duplicate hero entities with no PLAYER_ID at all
                # accumulate in GRAVEYARD with a stale place tag, and one of
                # those must never displace an identified player's row.
                player_id = tag(entity, GameTag.PLAYER_ID)
                prev = places.get(place)
                if prev is not None and prev.player_id and not player_id:
                    continue
                health = tag(entity, GameTag.HEALTH) - tag(entity, GameTag.DAMAGE)
                # Alive opponents' heroes rest in SETASIDE — only the current
                # pairing is in PLAY — so "not in PLAY" is where you sit
                # between fights, not death. Elimination reads as hp<=0
                # (where dead heroes reliably land) or GRAVEYARD, and is then
                # latched: a ghost fight can hand the entity full HP back.
                dead = health <= 0 or tag(entity, GameTag.ZONE) == Zone.GRAVEYARD
                if dead and player_id:
                    self._dead_player_ids.add(player_id)
                dead = dead or player_id in self._dead_player_ids
                places[place] = ev.Standing(
                    place=place,
                    player_id=player_id,
                    hero_card_id=entity.card_id,
                    health=health,
                    armor=tag(entity, GameTag.ARMOR),
                    tier=_tavern_tier(entity),
                    dead=dead,
                    you=bool(friendly) and tag(entity, GameTag.CONTROLLER) == friendly,
                )
        standings = tuple(places[p] for p in sorted(places))
        if standings and standings != self._standings:
            self._standings = standings
            self._emit(ev.Standings(places=standings))

    def friendly_placement(self) -> int | None:
        """Final placement from any friendly hero entity, regardless of zone.

        A dead hero sits in GRAVEYARD when the placement tag lands, so the
        in-play lookup can't see it.
        """
        fid = self.friendly_player_id()
        if fid is None:
            return None
        best = None
        for entity in self.game.entities:
            if (
                isinstance(entity, Card)
                and entity.type == CardType.HERO
                and tag(entity, GameTag.CONTROLLER) == fid
                and tag(entity, GameTag.PLAYER_LEADERBOARD_PLACE)
            ):
                if best is None or entity.id > best.id:
                    best = entity
        return tag(best, GameTag.PLAYER_LEADERBOARD_PLACE) if best else None

    def _maybe_emit_end(self):
        if self._end_emitted:
            return
        placement = self.friendly_placement()
        if placement:
            self._end_emitted = True
            self._emit(ev.GameEnd(placement=placement))

    def finalize(self):
        """Flush a pending GameEnd even if no placement ever appeared."""
        if self._ended and not self._end_emitted:
            self._end_emitted = True
            self._emit(ev.GameEnd(placement=None))


@dataclass
class _GameTrack:
    tree: object
    exporter: BGExporter
    cursor: int = 0
    started: bool = False


@dataclass
class LiveGameProcessor:
    """Feed Power.log lines in, drain typed BG events out."""

    parser: LogParser = field(default_factory=LogParser)
    _tracks: list[_GameTrack] = field(default_factory=list)
    _queue: deque = field(default_factory=deque)

    def feed(self, lines: list[str]) -> list[ev.Event]:
        shop_ready = 0
        for line in lines:
            # Non-log content (blank lines, truncation banners) has no
            # "D hh:mm:ss" prefix — skip it without ceremony.
            if not line or line[0] not in "DWE" or len(line) < 2 or line[1] != " ":
                continue
            if _ANIMATION_SHOP.search(line):
                shop_ready += 1
            # A session log holds many games, but hslog's player registry
            # chokes when battletags reappear with new player ids. Each
            # CREATE_GAME gets a completely fresh parser instead. Check
            # parser.games (not _tracks): within one large batch the tracks
            # only materialize at drain time.
            if line.endswith("- CREATE_GAME") and self.parser.games:
                self._register_and_advance()  # flush the finished game's events
                if self._tracks:
                    self._tracks[-1].exporter.finalize()
                self.parser = LogParser()
                self._tracks = []
            try:
                self.parser.read_line(line)
            except Exception:
                log.exception("parser choked on line: %r", line[:200])
        out = self._drain()
        # Appended after the batch's parsed events so a combat's resolution
        # always precedes its animation finishing. A live poll covers a
        # fraction of a second, so nothing else can slip between them.
        out.extend(ev.ShopReady() for _ in range(shop_ready))
        return out

    def _register_and_advance(self) -> None:
        for pt in self.parser.games[len(self._tracks):]:
            if self._tracks:
                self._tracks[-1].exporter.finalize()  # new game: flush pending end
            self._tracks.append(
                _GameTrack(tree=pt, exporter=BGExporter(pt, self._queue.append, self.parser.player_manager))
            )
        # Advance every track: a single large batch can contain whole games
        # before the current one; finished tracks cost nothing to revisit.
        for track in self._tracks:
            self._advance(track)

    def _drain(self) -> list[ev.Event]:
        self._register_and_advance()
        out = list(self._queue)
        self._queue.clear()
        return out

    def _advance(self, track: _GameTrack):
        packets = track.tree.packets
        advanced = False
        while track.cursor < len(packets):
            packet = packets[track.cursor]
            if not self._is_complete(packet, is_last=track.cursor == len(packets) - 1):
                break
            try:
                track.exporter.export_packet(packet)
            except Exception:
                log.exception("export failed for packet %r", type(packet).__name__)
            track.cursor += 1
            advanced = True
            if not track.started and track.exporter.game is not None:
                track.started = True
                game_type = self.parser.game_meta.get("GameType")
                start = getattr(track.tree, "start_time", None)
                self._queue.append(
                    ev.GameStart(
                        game_type=int(game_type) if game_type else None,
                        log_id=start.isoformat() if start else None,
                    )
                )
            if track.started:
                # Hero identity must be caught while the real hero is in play;
                # combat snapshots must stay on their authoritative packet
                # boundary.  The retained display projections below are safe
                # to coalesce to the final state of this input batch.
                track.exporter.maybe_emit_hero()
                track.exporter.maybe_emit_combat()
        if track.started and advanced:
            track.exporter.maybe_emit_standings()
            track.exporter.maybe_emit_buffs()
            track.exporter.maybe_emit_shop_board()

    @staticmethod
    def _is_complete(packet, is_last: bool) -> bool:
        """Whether a top-level packet is safe to export.

        Blocks/sub-spells are complete once ended. Anything else is complete
        when a later packet exists; the trailing packet may still accrue
        follow-up lines (FULL_ENTITY tag lines, CREATE_GAME players) unless
        it is an inherently single-line packet like TAG_CHANGE.
        """
        ended = getattr(packet, "ended", None)
        if ended is not None:
            return ended
        return not is_last or isinstance(packet, hspackets.TagChange)

    @property
    def current_exporter(self) -> BGExporter | None:
        return self._tracks[-1].exporter if self._tracks else None
