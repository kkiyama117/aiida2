# aiida2

AiiDA 2.9.0 + PostgreSQL + RabbitMQ in a single Apptainer container, built
from the official `aiidateam/aiida-core-with-services` image.

The calculation layer (KUDPC `sp` Slurm plugin, QE/Gaussian workflows) is
not in the tree yet — what is here is a working service container.

## Build

```console
$ apptainer build --fakeroot containerfiles/aiida.sif containerfiles/aiida.def
```

## Run

`run.py` wraps the flags the image needs. It is plain Python 3, standard
library only.

```console
$ ./run.py                      # services up + interactive bash (foreground)
$ ./run.py verdi status         # services up, one command, then down
$ ./run.py up                   # same session, in the background
$ ./run.py attach               # shell alongside a running container
$ ./run.py attach verdi status  # ... or one command in it
$ ./run.py status               # is a container running?
$ ./run.py down                 # stop the background container
```

`up` starts the very same foreground session with `sleep infinity` as its
command, detached from your terminal, and waits until s6 reports every
service up. `down` sends it a `SIGTERM`, which s6 turns into an ordered
shutdown.

`attach` opens a *second* container with `apptainer exec`, which skips the
runscript: no s6, no services, nothing started or stopped. It reaches the
running container's PostgreSQL and RabbitMQ because Apptainer shares the host
network namespace, and its `.aiida` directory because that is bind-mounted.
It is a separate PID and mount namespace, though — see
[docs/warnings.md](docs/warnings.md).

Underneath, a session is:

```console
$ apptainer run --pid --no-init --writable-tmpfs \
      -B <data>/.aiida:/home/aiida/.aiida \
      -B <data>/.postgresql:/home/aiida/.postgresql \
      -B <data>/.rabbitmq:/home/aiida/.rabbitmq \
      -B <data>/aiida_run:/home/aiida/aiida_run \
      containerfiles/aiida.sif [COMMAND]
```

- `--pid --no-init` — s6-overlay supervises the services and must be PID 1.
- `--writable-tmpfs` — s6 needs a writable `/run`.
- Only the state directories are bind-mounted. `/home/aiida` itself must stay
  the image's, because aiida-core is `pip --user` installed into
  `/home/aiida/.local`.

Any host user id works; the container does not switch users. The base image is
pinned by digest, so a rebuild always produces the same contents — see
[docs/warnings.md](docs/warnings.md) for how to bump it.

State lives in `data/container/home` (git-ignored); set `AIIDA_DATA_HOME` to
use another directory. One container per data directory: `run.py` holds an
`flock` on `<data>/.lock` for as long as the session lives.

## Limitations

- **It is a session, not a service.** When the command exits, s6 stops the
  AiiDA daemon, PostgreSQL and RabbitMQ. `./run.py up` keeps that session
  alive in the background, but it is still an ordinary process: it dies with
  the machine and nothing restarts it. `apptainer instance start` is not an
  option — s6-overlay cannot be PID 1 in an instance.
- **RabbitMQ uses the default ports** (5672 / 25672 / 4369) on the host
  network namespace. It will fail on a machine that already runs a broker.
  PostgreSQL was moved to 5433; RabbitMQ was not.
- **No authentication isolation.** PostgreSQL uses `trust` on localhost and
  RabbitMQ uses `guest`/`guest`, both reachable by any user on the host.

Details and smaller notes: [docs/warnings.md](docs/warnings.md).

## License

MIT — see [LICENSE](LICENSE). AiiDA itself is MIT, and the base image bundles
PostgreSQL (PostgreSQL License) and RabbitMQ (MPL 2.0); this repository only
covers the definition file and the wrapper script.
