from bgtracker.config import Config
from bgtracker.logwatch.tailer import IDLE_AFTER, Tailer, poll_delay


def test_reads_appended_lines(tmp_path):
    f = tmp_path / "Power.log"
    tailer = Tailer(f)
    assert tailer.read_new_lines() == []  # file doesn't exist yet

    f.write_bytes(b"one\ntwo\npart")
    assert tailer.read_new_lines() == ["one", "two"]
    assert tailer.read_new_lines() == []  # partial line stays buffered

    with f.open("ab") as fh:
        fh.write(b"ial\nthree\n")
    assert tailer.read_new_lines() == ["partial", "three"]


def test_handles_truncation(tmp_path):
    f = tmp_path / "Power.log"
    f.write_bytes(b"aaaa\nbbbb\n")
    tailer = Tailer(f)
    tailer.read_new_lines()
    f.write_bytes(b"new\n")  # rewritten shorter
    assert tailer.read_new_lines() == ["new"]


def test_crlf(tmp_path):
    f = tmp_path / "Power.log"
    f.write_bytes(b"line\r\n")
    assert Tailer(f).read_new_lines() == ["line"]


def test_bounded_reads_preserve_lines_and_the_historical_boundary(tmp_path):
    """A large existing log is catch-up until the original EOF, even when
    drained over several reads that split in the middle of a line."""
    f = tmp_path / "Power.log"
    f.write_bytes(b"one\ntwo\nthree\n")
    tailer = Tailer(f)

    assert tailer.read_new_lines(max_bytes=5) == ["one"]
    assert tailer.last_read_historical is True
    assert tailer.catchup_completed is False
    assert tailer.backlogged is True

    assert tailer.read_new_lines(max_bytes=5) == ["two"]
    assert tailer.last_read_historical is True
    assert tailer.catchup_completed is False
    assert tailer.backlogged is True

    assert tailer.read_new_lines(max_bytes=5) == ["three"]
    assert tailer.last_read_historical is True
    assert tailer.catchup_completed is True
    assert tailer.backlogged is False

    with f.open("ab") as fh:
        fh.write(b"live\n")
    assert tailer.read_new_lines(max_bytes=5) == ["live"]
    assert tailer.last_read_historical is False
    assert tailer.catchup_completed is False


def test_a_file_created_after_the_tailer_is_live(tmp_path):
    """An empty first drain must not make the game's first later lines old."""
    f = tmp_path / "Power.log"
    tailer = Tailer(f)
    assert tailer.read_new_lines(max_bytes=5) == []

    f.write_bytes(b"live\n")
    assert tailer.read_new_lines(max_bytes=5) == ["live"]
    assert tailer.last_read_historical is False
    assert tailer.catchup_completed is False


def test_a_truncated_log_is_rebuilt_as_history(tmp_path):
    """Re-reading a rewritten log must not simulate its old combats again."""
    f = tmp_path / "Power.log"
    f.write_bytes(b"old one\nold two\n")
    tailer = Tailer(f)
    assert tailer.read_new_lines() == ["old one", "old two"]
    assert tailer.catchup_completed is True

    f.write_bytes(b"new\n")
    assert tailer.read_new_lines() == ["new"]
    assert tailer.last_read_historical is True
    assert tailer.catchup_completed is True
    assert tailer.backlogged is False


def test_poll_backs_off_once_the_log_goes_quiet():
    """`poll_idle` has to actually reach the sleep.

    The live loop used to sleep `poll_active` unconditionally, so the log was
    stat()ed four times a second forever — including all the time spent at the
    menu — while the settings window advertised an idle interval that did
    nothing at all.
    """
    cfg = Config(poll_active=0.25, poll_idle=2.0)
    assert poll_delay(cfg, 0) == 0.25
    assert poll_delay(cfg, IDLE_AFTER - 1) == 0.25
    assert poll_delay(cfg, IDLE_AFTER) == 2.0


def test_poll_intervals_are_read_fresh_from_the_config():
    """Both are channel NONE settings: mutating the shared Config is the whole
    apply mechanism, so nothing may be captured at import or call time."""
    cfg = Config(poll_active=0.25, poll_idle=2.0)
    cfg.poll_active, cfg.poll_idle = 0.05, 5.0
    assert poll_delay(cfg, 0) == 0.05
    assert poll_delay(cfg, IDLE_AFTER) == 5.0
