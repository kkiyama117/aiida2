#!/usr/bin/env python3
"""Start and use the AiiDA container.

s6-overlay supervises PostgreSQL, RabbitMQ and the AiiDA daemon, so it has to
be PID 1 (--pid --no-init) and needs a writable /run (--writable-tmpfs).

There is no daemon mode in Apptainer for this image: `apptainer instance
start` keeps its own process as PID 1 and s6 aborts, which is why the
definition file has no %startscript.  `up` therefore just puts the same
foreground session in the background and leaves it running; `attach` opens a
*second*, service-less container that talks to those services over the shared
host network namespace.  See docs/warnings.md.

Only the standard library is used: run it with any python3, no venv.
"""

import argparse
import fcntl
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
SIF = DIR / "containerfiles" / "aiida.sif"
DATA = Path(os.environ.get("AIIDA_DATA_HOME") or DIR / "data" / "container" / "home")

# Only the state directories are bind-mounted.  Mounting the whole /home/aiida
# would hide the image's own home, and aiida-core lives there: it is
# pip --user installed into /home/aiida/.local.
BINDS = [".aiida", ".postgresql", ".rabbitmq", "aiida_run"]

# Extra mounts from config/binds.conf: one `src:dst[:ro]` per line, '#'
# comments. Credentials stay out of the image and out of git; no file means
# no extra mounts -- the default session carries no credentials.
BINDS_CONF = DIR / "config" / "binds.conf"
# Destinations that would hide aiida-core or the managed profile state.
PROTECTED_DESTS = ("/home/aiida/.local", "/home/aiida/.aiida")

LOCK = DATA / ".lock"
LOG = DATA / "container.log"

# s6 logs this once every service is up and just before it hands over to the
# container command -- a more honest readiness signal than "something answers
# on the port", which on a shared network namespace may be a foreign server.
READY_MARKER = "legacy-services successfully started"


def die(*lines):
    for line in lines:
        print(line, file=sys.stderr)
    raise SystemExit(1)


def apptainer():
    exe = shutil.which("apptainer")
    if exe is None:
        die("apptainer not found in PATH.")
    if not SIF.is_file():
        die(
            f"{SIF} not found.  Build it with:",
            f"  apptainer build --fakeroot {SIF} {DIR / 'containerfiles' / 'aiida.def'}",
        )
    return exe


def extra_binds(path=None):
    """Apptainer mounts from config/binds.conf, or [] if the file is absent.

    A missing source or a destination that shadows /home/aiida/.local or
    /home/aiida/.aiida is an error, not a silent mount: Apptainer would
    otherwise create an empty directory at the source, and the shadowed
    paths hold the installation and the managed state.
    """
    if path is None:
        path = BINDS_CONF
    if not path.is_file():
        return []
    mounts = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        src, sep, rest = line.partition(":")
        dst, _, mode = rest.partition(":")
        if not sep or not src or not dst or mode not in ("", "ro"):
            die(f"{path}:{lineno}: expected 'src:dst[:ro]': {line}")
        src = os.path.expanduser(src)
        if not os.path.isabs(src):
            die(f"{path}:{lineno}: source must be absolute: {src}")
        if not os.path.isabs(dst):
            die(f"{path}:{lineno}: destination must be absolute: {dst}")
        if not os.path.exists(src):
            die(f"{path}:{lineno}: bind source does not exist: {src}")
        if any(dst == p or dst.startswith(p + "/") for p in PROTECTED_DESTS):
            die(f"{path}:{lineno}: destination shadows a managed path: {dst}")
        mounts.append(f"{src}:{dst}" if not mode else f"{src}:{dst}:ro")
    return mounts


def binds():
    args = []
    for name in BINDS:
        (DATA / name).mkdir(parents=True, exist_ok=True)
        args += ["-B", f"{DATA / name}:/home/aiida/{name}"]
    # Credentials from config/binds.conf ride along for up, shell and attach.
    for mount in extra_binds():
        args += ["-B", mount]
    return args


def session_cmd(command):
    """`apptainer run`: s6 comes up, runs COMMAND, tears everything down."""
    return [apptainer(), "run", "--pid", "--no-init", "--writable-tmpfs",
            *binds(), str(SIF), *command]


def holder():
    """PID of the container holding the data directory, or None if free.

    One container per data directory; two would corrupt the PostgreSQL
    cluster and the RabbitMQ mnesia store.  The lock is an flock on the open
    file description, so it is released by the kernel when the container dies
    -- no stale pid files to clean up.  The pid inside the file is only there
    so `down` and `status` have something to report.
    """
    DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            return int(os.read(fd, 32).split()[0])
        except (ValueError, IndexError):
            return 0  # locked by someone who did not write a usable pid
    finally:
        os.close(fd)  # closing drops the lock we may have just taken
    return None


def acquire():
    """Take the lock and return its fd, ready to be inherited by the container."""
    DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pid = holder()
        die(f"another container is already using {DATA} (pid {pid or '?'})")
    os.set_inheritable(fd, True)
    return fd


def stamp(fd, pid):
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"{pid}\n".encode(), 0)


def cmd_session(args):
    """Foreground: replace ourselves with the container, lock and all."""
    fd = acquire()
    stamp(fd, os.getpid())  # exec keeps the pid, so this stays correct
    argv = session_cmd(args.command)
    os.execv(argv[0], argv)


def cmd_up(args):
    fd = acquire()
    # Truncated, not appended: the interesting part of this log is always the
    # boot that is running now.
    log = open(LOG, "wb")
    # start_new_session detaches from our terminal and process group, so the
    # container survives the shell that started it and ignores its ^C.
    proc = subprocess.Popen(
        session_cmd(["sleep", "infinity"]),
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, pass_fds=(fd,),
    )
    stamp(fd, proc.pid)
    print(f"container started (pid {proc.pid}), log: {LOG}")
    if args.no_wait:
        return
    print("waiting for services ...", flush=True)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if READY_MARKER in LOG.read_text(errors="replace"):
            print("services are up -- attach with:  ./run.py attach")
            return
        if proc.poll() is not None:
            sys.stderr.write(LOG.read_text(errors="replace")[-2000:])
            die("", f"the container exited with status {proc.returncode} before"
                    " its services came up (tail of the log above)")
        time.sleep(1)
    die(f"services did not come up within {args.timeout}s; see {LOG}",
        "The container is still running -- stop it with ./run.py down")


def cmd_attach(args):
    """A second container, without s6, sharing the running one's services.

    `apptainer exec` skips the runscript, so nothing is started or stopped
    here and the lock stays with the session container.  Apptainer shares the
    host network namespace, which is what makes this work: verdi reaches
    PostgreSQL on localhost:PGPORT and RabbitMQ on 5672 exactly as it does
    inside the session.  It is a different PID and mount namespace, though --
    ~/.erlang.cookie is regenerated per container, so rabbitmqctl and
    rabbitmq-diagnostics cannot reach the broker from here.
    """
    if holder() is None:
        die(f"no container is using {DATA}.",
            "Start one with:  ./run.py up")
    # --quiet drops apptainer's INFO chatter, including the harmless
    # fuse-overlayfs teardown error that --writable-tmpfs prints when this
    # shell exits; FATAL and WARNING still get through.
    argv = [apptainer(), "--quiet", "exec", "--writable-tmpfs", *binds(),
            str(SIF), *(args.command or ["bash"])]
    os.execv(argv[0], argv)


def cmd_down(args):
    pid = holder()
    if pid is None:
        print(f"no container is using {DATA}")
        return
    if not pid:
        die(f"{DATA} is locked by a container that did not record its pid;"
            " stop it by hand")
    # SIGTERM reaches s6 through the Apptainer runtime parent and shuts the
    # services down in order; the lock is released when the process dies.
    os.kill(pid, signal.SIGTERM)
    print(f"stopping container (pid {pid}) ...", flush=True)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if holder() is None:
            print("stopped")
            return
        time.sleep(1)
    die(f"pid {pid} did not stop within {args.timeout}s")


def cmd_status(args):
    pid = holder()
    if pid is None:
        print(f"not running ({DATA})")
        raise SystemExit(1)
    print(f"running (pid {pid or '?'}), data: {DATA}")
    port = int(os.environ.get("AIIDA_POSTGRES_PORT", 5433))
    for name, p in (("postgresql", port), ("rabbitmq", 5672)):
        with socket.socket() as s:
            s.settimeout(1)
            ok = s.connect_ex(("localhost", p)) == 0
        # Host-side check only: on a shared network namespace an open port is
        # not proof that it is *our* server.  Use `./run.py attach verdi
        # status` for the real answer.
        print(f"  {name:<10} localhost:{p} {'open' if ok else 'closed'}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="With no subcommand, COMMAND is run in a foreground session:\n"
               "  ./run.py                 services up + interactive bash\n"
               "  ./run.py verdi status    services up, one command, then down",
    )
    sub = parser.add_subparsers(dest="mode")

    p = sub.add_parser("shell", help="foreground session (default)")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("up", help="start a session in the background")
    p.add_argument("--no-wait", action="store_true",
                   help="return as soon as the container is spawned")
    p.add_argument("--timeout", type=int, default=600,
                   help="seconds to wait for the services (default: 600)")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("attach", help="shell into the running container's services")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_attach)

    p = sub.add_parser("down", help="stop the running container")
    p.add_argument("--timeout", type=int, default=120,
                   help="seconds to wait for a clean shutdown (default: 120)")
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("status", help="report whether a container is running")
    p.set_defaults(func=cmd_status)

    # `./run.py verdi status` has to keep working, so anything that is not a
    # subcommand is a command for a foreground session.
    argv = sys.argv[1:]
    if argv and argv[0] not in sub.choices and not argv[0].startswith("-"):
        argv = ["shell", *argv]
    args = parser.parse_args(argv)
    if args.mode is None:
        args.command = []
        cmd_session(args)
    args.func(args)


if __name__ == "__main__":
    main()
