"""Host-side tests for run.py's config/binds.conf support.

Standard library only: runnable with pytest or directly
(`python3 tests/test_run_binds.py`).  Temporary directories stand in for
~/.ssh, so nothing here touches a real home directory.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run


def check(text, root):
    """Write TEXT as a binds.conf under ROOT and parse it."""
    conf = root / "binds.conf"
    conf.write_text(text)
    return run.extra_binds(conf)


def dies(fn, *args):
    """Assert that FN(*ARGS) exits through run.die()."""
    try:
        fn(*args)
    except SystemExit:
        return
    raise AssertionError("expected SystemExit, nothing was raised")


def test_comments_and_blank_lines_ignored():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        key = td / "key"
        key.write_text("x")
        mounts = check(
            f"""
            # comment
              # indented comment

            {key}:/home/aiida/.ssh/id_kudpc:ro

            {key}:/home/aiida/license
            """,
            td,
        )
        assert mounts == [
            f"{key}:/home/aiida/.ssh/id_kudpc:ro",
            f"{key}:/home/aiida/license",
        ]


def test_home_expansion():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        key = td / "key"
        key.write_text("x")
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(td)
        try:
            mounts = check("~/key:/home/aiida/.ssh/id_kud0:ro\n", td)
        finally:
            if old is None:
                del os.environ["HOME"]
            else:
                os.environ["HOME"] = old
        assert mounts == [f"{key}:/home/aiida/.ssh/id_kud0:ro"]


def test_missing_source_fails_loudly():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        conf = td / "binds.conf"
        conf.write_text(f"{td}/no-such-key:/home/aiida/x:ro\n")
        dies(run.extra_binds, conf)


def test_protected_destinations_rejected():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        key = td / "key"
        key.write_text("x")
        for i, dst in enumerate(
            (
                "/home/aiida/.local",
                "/home/aiida/.local/foo",
                "/home/aiida/.aiida",
                "/home/aiida/.aiida/profile",
            )
        ):
            conf = td / f"c{i}.conf"
            conf.write_text(f"{key}:{dst}:ro\n")
            dies(run.extra_binds, conf)


def test_invalid_lines_fail_loudly():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for i, bad in enumerate(("nodirs", "src:", ":dst", "/a:/b:rw")):
            conf = td / f"c{i}.conf"
            conf.write_text(bad + "\n")
            dies(run.extra_binds, conf)


def test_absent_file_adds_no_mounts():
    with tempfile.TemporaryDirectory() as td:
        assert run.extra_binds(Path(td) / "nope.conf") == []


def test_binds_appends_conf_mounts():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        key = td / "key"
        key.write_text("x")
        old_data, old_conf = run.DATA, run.BINDS_CONF
        run.DATA, run.BINDS_CONF = td / "data", td / "binds.conf"
        try:
            run.BINDS_CONF.write_text(f"{key}:/home/aiida/.ssh/key:ro\n")
            args = run.binds()
        finally:
            run.DATA, run.BINDS_CONF = old_data, old_conf
        assert len(args) == 2 * (len(run.BINDS) + 1)
        assert args[-2:] == ["-B", f"{key}:/home/aiida/.ssh/key:ro"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"{len(tests)} tests passed")
