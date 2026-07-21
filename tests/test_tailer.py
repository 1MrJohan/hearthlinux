from bgtracker.logwatch.tailer import Tailer


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
