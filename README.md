# aiida2

AiiDA 2.9.0 + PostgreSQL + RabbitMQ in a single Apptainer container, built
from the official `aiidateam/aiida-core-with-services` image.

The calculation layer is here: `aiida_plugins/aiida-slurm-rsc` (the KUDPC
`sp` scheduler) is in the tree and baked into the image, alongside
upstream `aiida-gaussian` 2.2.0 (`gaussian` CalcJob, `gaussian.base`
parser) installed from PyPI with its dependency tree at build time. The
workflows are not — see docs/gaussian-plan.md.

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


## Site setup and one calculation

Everything site-specific lives in two git-ignored config files; committed
`.example` versions document the format. Nothing is bound or configured by
default — a fresh clone carries no credentials. **First time here? Follow
[docs/site-setup.md](docs/site-setup.md)** — a step-by-step walkthrough from
SSH key to the first submitted calculation.

### `config/binds.conf`

Extra Apptainer bind mounts appended by `run.py` for `up`, `shell` and
`attach` alike: one `src:dst[:ro]` per line, `#` comments, `~` expands. A
missing source fails loudly instead of letting Apptainer create an empty
directory, and destinations may not shadow `/home/aiida/.local` (aiida-core)
or `/home/aiida/.aiida`. For cluster access, bind your key and `known_hosts`
as individual read-only files (see
[`config/binds.example.conf`](config/binds.example.conf)); the host key must
already be registered — AiiDA's default `RejectPolicy` stays.

### `config/site.yaml`

Read inside the container by `tools/setup_site.py`, which creates/updates the
Computer (`sp`, scheduler `slurm_rsc`, transport `core.ssh`,
`mpirun_command: [srun]`) and the `g16` code idempotently — run it twice,
identities are preserved. `mail_user` and `default_queue` are stored as
Computer properties; the nested `ssh:` mapping becomes transport auth
parameters. See [`config/site.example.yaml`](config/site.example.yaml).

```console
$ ./run.py attach python3 tools/setup_site.py
```

### Submitting: dry run first

A calculation is one small YAML ([`examples/h2o_opt.yaml`](examples/h2o_opt.yaml)):
code, structure file, route, and one resource block that is the single source
for both the Slurm allocation and Gaussian's link0 block.
`tools/submit.py --dry-run` renders `_aiidasubmit.sh` and `aiida.inp` locally
(no SSH, no cluster) so the shape can be checked before touching KUDPC;
`tests/test_dry_run_integration.py` asserts exactly that shape.

```console
$ ./run.py attach python3 tools/submit.py examples/h2o_opt.yaml --dry-run
$ ./run.py attach python3 tools/submit.py examples/h2o_opt.yaml   # real submit
```

Resource derivation rules:

- `%nprocshared = num_cores_per_mpiproc`; Slurm gets
  `--rsc p=1:t=C:c=C:m=MG` from the same numbers.
- `%mem = memory_gb - 1` GiB, never below `memory_gb: 2`: `%mem` covers
  *dynamic* memory only — executable, static memory, thread stacks and I/O
  buffers sit on top — so the headroom is a fixed 1 GiB, not a percentage
  (a percentage wastes progressively more as allocations grow). The constant
  stays provisional: KUDPC's accounting reports `MaxRSS = 0` for every step
  (first real job 8449824 verified), so there is no measured peak to
  calibrate against; revisit only if a larger job dies from memory.
  Always emit an explicit unit — a bare `%mem=4` means four 8-byte words.
- `num_mpiprocs_per_machine` must be 1: Gaussian uses one shared-memory
  process; `withmpi=True` + `mpirun_command: [srun]` prefixes `g16` with
  `srun`; it does not mean multiple MPI processes.

## Limitations
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
