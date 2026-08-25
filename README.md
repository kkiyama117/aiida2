# aiida2

AiiDA 2.9.0 + PostgreSQL + RabbitMQ in a single Apptainer container, built
from the official `aiidateam/aiida-core-with-services` image.

The calculation layer is arriving: `aiida_plugins/aiida-slurm-rsc` (the
KUDPC `sp` scheduler) is in the tree and baked into the image. The QE and
Gaussian workflows are not.

## Build

```console
$ apptainer build --fakeroot containerfiles/aiida.sif containerfiles/aiida.def
```

## Plugins

`aiida_plugins/` holds the AiiDA plugins this project ships. They are
installed **at build time**, into the same `pip --user` site as aiida-core
(`/home/aiida/.local`): that directory belongs to the image, so a runtime
`pip install` would land in the `--writable-tmpfs` overlay and disappear with
the session. Editing a plugin therefore means rebuilding the image.

| plugin | entry point | what it does |
| --- | --- | --- |
| `aiida-slurm-rsc` | `slurm_rsc` (`aiida.schedulers`) | Slurm scheduler for KUDPC Camphor (`sp`). The cluster forbids `--nodes`, `--ntasks*`, `--cpus-per-task`, `--mem` and `--qos` and wants `#SBATCH --rsc p=N:t=N:c=N:m=NG` instead, so the plugin subclasses `SlurmScheduler` and reimplements only the submit-script header; `squeue`/`sacct` parsing is inherited. |

Each plugin's tests run in the image, where aiida-core is; pytest is installed
alongside them.

```console
$ ./run.py attach verdi plugin list aiida.schedulers        # slurm_rsc is listed
$ ./run.py attach bash -c 'cd /opt/aiida_plugins/aiida-slurm-rsc && python3 -m pytest'
```

A computer picks it up at setup time — `verdi computer setup --scheduler
slurm_rsc` — and nothing else changes: calculations keep declaring ordinary
AiiDA `resources`, and the plugin maps
`num_machines`/`num_mpiprocs_per_machine × num_cores_per_mpiproc`/
`max_memory_kb` onto `p`/`t`=`c`/`m` (memory rounded up to whole GiB).

It also ships **mail notification**, which aiida-core has no option for:
`mail_scheduler_commands()` renders the `#SBATCH --mail-*` lines into
`metadata.options.custom_scheduler_commands`, taking the address from the
`Computer`'s own metadata. Nothing is sent until that property is set. See
[`aiida_plugins/aiida-slurm-rsc/README.md`](aiida_plugins/aiida-slurm-rsc/README.md).

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
