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

```console
$ ./run.sh              # services up + interactive bash
$ ./run.sh verdi status
```

`run.sh` wraps the flags the image needs:

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

State lives in `data/container/home` (git-ignored); set `AIIDA_DATA_HOME` to
use another directory.

## Limitations

- **It is a session, not a service.** When the command exits, s6 stops the
  AiiDA daemon, PostgreSQL and RabbitMQ. Long-running workflows do not
  survive logout. `apptainer instance start` does not help — s6-overlay
  cannot be PID 1 in an instance.
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
