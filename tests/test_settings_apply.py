"""Changing a setting has to reach the running process, not just the file.

Every setting applies live, which means each one has a path from the settings
window to the object that actually uses it. These tests pin down that path: the
shared-Config mutation everything depends on, the channel dispatch, and the
lock that stops a sidecar restart eating a combat forecast.

Every test writes to tmp_path. Nothing here may touch the real config file.
"""

from __future__ import annotations

import asyncio

import pytest

from bgtracker.config import Config, load_config
from bgtracker.settings import HOVER, OVERLAY_REBUILD, SIM, SettingsService
from bgtracker.sim.client import SimClient


@pytest.fixture
def service(tmp_path):
    return SettingsService(Config(), path=tmp_path / "config.toml")


class Recorder:
    """Captures the (channel, keys) an applier was called with."""

    def __init__(self):
        self.calls: list[set[str]] = []

    def __call__(self, keys):
        self.calls.append(set(keys))


# -- the shared Config -------------------------------------------------
def test_set_mutates_the_config_in_place(service):
    original = service.cfg
    service.set("sim_count", 4000)
    # Identity, not equality: the tail loop and the overlay hold this exact
    # object, so replacing it would leave them reading stale values forever.
    assert service.cfg is original
    assert original.sim_count == 4000


def test_set_persists_to_disk(service):
    service.set("sim_count", 4000)
    assert load_config(service.path).sim_count == 4000


def test_poll_interval_is_visible_through_the_shared_config(service):
    # The tail loop does `await asyncio.sleep(cfg.poll_active)` against this
    # same object every iteration — no applier is needed or wired.
    cfg = service.cfg
    service.set("poll_active", 1.5)
    assert cfg.poll_active == 1.5


def test_a_no_op_set_changes_nothing_and_says_so(service):
    seen = Recorder()
    service.subscribe(SIM, seen)
    assert service.set("sim_count", Config().sim_count) is False
    assert seen.calls == [], "re-setting the current value must not churn appliers"


def test_values_are_coerced_before_they_are_stored(service):
    service.set("sim_count", 10**9)
    assert service.cfg.sim_count == 50000
    assert load_config(service.path).sim_count == 50000


def test_setting_none_removes_the_key_so_the_default_returns(service):
    service.set("overlay_scale", 2.0)
    assert load_config(service.path).overlay_scale == 2.0
    service.set("overlay_scale", None)
    assert service.cfg.overlay_scale is None
    # Absent, not `= None`: TOML has no null and the loader must fall back.
    assert "overlay_scale" not in service.path.read_text()


def test_a_path_setting_is_written_as_a_string(tmp_path):
    service = SettingsService(Config(), path=tmp_path / "config.toml")
    service.set("hearthstone_dir", str(tmp_path))
    assert load_config(service.path).hearthstone_dir == tmp_path


# -- dispatch ----------------------------------------------------------
def test_a_change_reaches_only_its_own_channel(service):
    sim, hover = Recorder(), Recorder()
    service.subscribe(SIM, sim)
    service.subscribe(HOVER, hover)
    service.set("sim_count", 4000)
    assert sim.calls == [{"sim_count"}]
    assert hover.calls == []


def test_set_many_dispatches_once_per_channel(service):
    sim = Recorder()
    service.subscribe(SIM, sim)
    service.set_many({"sim_count": 4000, "sim_timeout": 9.0})
    # One call carrying both keys, not two calls — a reset would otherwise
    # rebuild the overlay once per key.
    assert sim.calls == [{"sim_count", "sim_timeout"}]


def test_set_many_writes_the_file_once(service, monkeypatch):
    import bgtracker.settings as settings_mod

    writes = []
    real = settings_mod.update_config_values
    monkeypatch.setattr(
        settings_mod, "update_config_values",
        lambda values, path=None: (writes.append(dict(values)), real(values, path))[1],
    )
    service.set_many({"sim_count": 4000, "hover_debug": True, "poll_idle": 3.0})
    assert len(writes) == 1


def test_a_failing_applier_does_not_strand_the_others(service, caplog):
    def explode(_keys):
        raise RuntimeError("boom")

    survivor = Recorder()
    service.subscribe(SIM, explode)
    service.subscribe(SIM, survivor)
    service.set("sim_count", 4000)
    assert survivor.calls == [{"sim_count"}], "one bad applier must not block the rest"
    assert "boom" in caplog.text


# -- resets ------------------------------------------------------------
def test_reset_restores_defaults_and_forgets_the_keys(service):
    service.set_many({"leaderboard_left_px": 341, "leaderboard_skew_px": -47})
    hover = Recorder()
    service.subscribe(HOVER, hover)
    service.reset_section("calibration")
    assert service.cfg.leaderboard_left_px == 0
    assert service.cfg.leaderboard_skew_px == 0
    assert hover.calls, "the hover window has to be told to rebuild"
    assert load_config(service.path).leaderboard_skew_px == 0


def test_reset_panel_positions_clears_extra_and_asks_for_a_rebuild(service):
    service.cfg.extra.update({"pos_hud_x": 1864, "pos_hud_y": 151})
    from bgtracker.config import update_config_values

    update_config_values({"pos_hud_x": 1864, "pos_hud_y": 151}, service.path)
    rebuild = Recorder()
    service.subscribe(OVERLAY_REBUILD, rebuild)

    assert service.reset_panel_positions() is True
    assert service.cfg.extra == {}
    assert "pos_hud_x" not in service.path.read_text()
    assert rebuild.calls == [{"pos_hud_x", "pos_hud_y"}]


def test_reset_all_returns_a_fresh_config(service):
    service.set_many({"sim_count": 4000, "hover_debug": True, "overlay_scale": 2.0})
    service.cfg.extra["pos_hud_x"] = 10
    service.reset_all()
    assert service.cfg == Config()


# -- the sim client ----------------------------------------------------
def test_apply_config_retargets_trials_and_timeout():
    sim = SimClient()
    cfg = Config(sim_count=1234, sim_timeout=9.5)
    sim.apply_config(cfg)
    assert (sim.sims, sim.timeout) == (1234, 9.5)


def test_the_new_trial_count_reaches_the_next_request():
    """The plumbing all the way to the sidecar payload, without spawning node."""
    sim = SimClient()
    sent = {}

    async def fake_request(payload, timeout, on_partial=None):
        sent.update(payload)
        return {
            "wonPercent": 50.0, "tiedPercent": 0.0, "lostPercent": 50.0,
            "averageDamageWon": 1.0, "averageDamageLost": 1.0,
            "won": 1, "tied": 0, "lost": 1,
        }

    sim._request = fake_request
    sim.apply_config(Config(sim_count=1234))
    asyncio.run(sim.simulate({}))
    assert sent["sims"] == 1234


def test_worker_change_waits_for_a_combat_in_flight():
    """A sidecar restart must queue behind a forecast, never kill it.

    `_request` holds `_lock` across its `readline()` await. If a worker change
    terminated the process without taking that lock, the awaiting request would
    raise "sidecar died mid-request" and that combat would show no odds at all.
    """
    async def scenario():
        sim = SimClient(workers=2)
        stopped = asyncio.Event()

        async def fake_stop():
            stopped.set()

        sim._stop = fake_stop
        sim._ensure_proc = lambda: asyncio.sleep(0)

        await sim._lock.acquire()          # stand in for a simulate() in flight
        task = asyncio.create_task(sim.reconfigure_workers(4))
        await asyncio.sleep(0)
        assert not stopped.is_set(), "restarted the sidecar under a live request"
        assert sim.workers == 2, "worker count moved before the lock was free"

        sim._lock.release()                # the combat finishes
        assert await task is True
        assert stopped.is_set() and sim.workers == 4

    asyncio.run(scenario())


def test_an_unchanged_worker_count_does_not_restart_anything():
    async def scenario():
        sim = SimClient(workers=4)
        sim._stop = lambda: pytest.fail("restarted for a no-op change")
        assert await sim.reconfigure_workers(4) is False

    asyncio.run(scenario())
