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
