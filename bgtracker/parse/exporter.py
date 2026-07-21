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
from collections import deque
from dataclasses import dataclass, field

from hearthstone.entities import Card, Game
from hearthstone.enums import CardType, GameTag, State, Zone
from hslog import LogParser
from hslog import packets as hspackets
from hslog.export import EntityTreeExporter

from bgtracker.parse import events as ev
from bgtracker.state.game import BoardSnapshot, project_player_board, tag

log = logging.getLogger(__name__)

BOB_HERO_ID = "TB_BaconShopBob"

SHOP = 1
COMBAT = 2


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

    def maybe_emit_hero(self):
        hero = self.friendly_hero()
        if hero is None or not hero.card_id:
            return
        # Placeholder hero entities (e.g. BaconPHhero) sit in play until the
        # pick resolves; wait for the real one, and re-emit on change.
        if "PH" in hero.card_id or hero.card_id == self._hero_emitted:
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
                    self.maybe_emit_combat()
                elif value == SHOP:
                    self._pending_combat = False
                    self._emit(ev.CombatEnd(snapshot=self.snapshot()))
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
        return entity

    def maybe_emit_standings(self):
        if not self._standings_dirty:
            return
        self._standings_dirty = False
        places: dict[int, tuple[int, int, str | None]] = {}
        for entity in self.game.entities:
            if not isinstance(entity, Card) or entity.type != CardType.HERO:
                continue
            if not entity.card_id or "PH" in entity.card_id or entity.card_id.startswith(BOB_HERO_ID):
                continue
            place = tag(entity, GameTag.PLAYER_LEADERBOARD_PLACE)
            if place:
                # PLAYER_ID is the stable per-player identity; the controller
                # is a shared slot. Newest entity wins (ghost copies linger).
                places[place] = (place, tag(entity, GameTag.PLAYER_ID), entity.card_id)
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
        for line in lines:
            # Non-log content (blank lines, truncation banners) has no
            # "D hh:mm:ss" prefix — skip it without ceremony.
            if not line or line[0] not in "DWE" or len(line) < 2 or line[1] != " ":
                continue
            # A session log holds many games, but hslog's player registry
            # chokes when battletags reappear with new player ids. Each
            # CREATE_GAME gets a completely fresh parser instead.
            if line.endswith("- CREATE_GAME") and self._tracks:
                self._tracks[-1].exporter.finalize()
                self.parser = LogParser()
                self._tracks = []
            try:
                self.parser.read_line(line)
            except Exception:
                log.exception("parser choked on line: %r", line[:200])
        return self._drain()

    def _drain(self) -> list[ev.Event]:
        for pt in self.parser.games[len(self._tracks):]:
            if self._tracks:
                self._tracks[-1].exporter.finalize()  # new game: flush pending end
            self._tracks.append(
                _GameTrack(tree=pt, exporter=BGExporter(pt, self._queue.append, self.parser.player_manager))
            )
        if self._tracks:
            self._advance(self._tracks[-1])
        out = list(self._queue)
        self._queue.clear()
        return out

    def _advance(self, track: _GameTrack):
        packets = track.tree.packets
        while track.cursor < len(packets):
            packet = packets[track.cursor]
            if not self._is_complete(packet, is_last=track.cursor == len(packets) - 1):
                break
            try:
                track.exporter.export_packet(packet)
            except Exception:
                log.exception("export failed for packet %r", type(packet).__name__)
            track.cursor += 1
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
                track.exporter.maybe_emit_hero()
                track.exporter.maybe_emit_combat()
                track.exporter.maybe_emit_standings()

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
