# Project review

This is a thin Apptainer wrapper around `aiidateam/aiida-core-with-services`. The real work is in `containerfiles/aiida.def` and `run.sh`: make the official s6 image run under a shared host network, keep state in a bind-mounted home, and shut RabbitMQ down cleanly.

The Apptainer-specific patches are the strongest part of the repo. The remaining problems are pin/reproducibility, silent `sed` failure, a docs/env mismatch, and the gap between the README and what is actually here.

## What is in good shape

The comments in `aiida.def` match real Apptainer failure modes, not generic Docker advice:

- `HOME=/home/aiida` — Apptainer otherwise points `HOME` at the host user and hides `pip --user` / the AiiDA profile.
- `S6_KEEP_ENV=1` — without this, `HOME` and `PGPORT` never reach the s6 tree.
- `SYSTEM_GID="$(id -g)"` — the image’s `fix-permissions` chgrps to gid 100, which is not mapped in the user namespace.
- Replacing `postgresql-prepare.sh` — stock `pg_isready` targets 5432 and s6 waits forever once Postgres is on 5433.
- RabbitMQ `down-signal=SIGTERM` — Erlang is started with `-B i`, so SIGINT never stops the broker and port 25672 is left held.
- Bounded `rabbitmq-diagnostics -t 5` — the 500 ms readiness probe no longer blocks for 60 s per attempt.
- `S6_CMD_WAIT_FOR_SERVICES_MAXTIME=600000` — fail after 10 minutes instead of hanging.

`run.sh` is the right shape: resolve the script dir, create the data home, pass `--pid --no-init --writable-tmpfs`, bind one directory, exec. `.gitignore` covers `data` and `*.sif`.

The official image already sets RabbitMQ `consumer_timeout` to `undefined`, so dropping the old Ubuntu-based `advanced.config` is correct.

## Findings

### 1. `From: …:latest` plus `sed` will break on the next rebuild

README claims AiiDA 2.9. The def pulls `latest`. Hub has `aiida-2.9.0`. `latest` already moved (there is a `postgresql-15` tag pushed recently).

The Postgres port patch is a `sed` on an exact line in `/etc/init/postgresql-init.sh`:

```text
sed -i 's|^   echo "unix_socket_directories = .*$|&\n   echo "port = ${PGPORT:-5433}" >> /home/${SYSTEM_USER}/.postgresql/postgresql.conf|' /etc/init/postgresql-init.sh
```

Upstream `main` is now a single-space indent, not three:

```text
 echo "unix_socket_directories = '/tmp'" >> /home/${SYSTEM_USER}/.postgresql/postgresql.conf
```

`sed` that matches nothing still exits 0. The image builds, Postgres stays on 5432, and you only notice when it clashes with a host instance — the original bug this patch exists to fix. There is no `grep`/`|| exit 1` after either `sed`.

Pin `aiida-2.9.0` (or a digest). Prefer copying the two init scripts into the repo and installing them over `sed`.

### 2. `AIIDA_HOME` in the docs is not the variable `run.sh` reads

`docs/warnings.md` says:

```text
Set `AIIDA_HOME` to use another directory.
```

`run.sh` reads:

```text
AIIDA_DATA_HOME=${AIIDA_DATA_HOME:-$DIR/data/container/home}
```

Setting `AIIDA_HOME` does nothing.

### 3. `./run.sh` is a session, not a daemon

`%startscript` is written for `apptainer instance start`, but `run.sh` uses `apptainer run`. Exit the shell and s6 tears down Postgres, RabbitMQ, and the AiiDA circus daemon. The local daemon log shows that: daemon up at 21:27:43, arbiter exiting at 21:27:45.

For QE/Gaussian on `sp` that is the wrong default. Either `run.sh` should start an instance, or the README should say this is an interactive session; workflows die when you logout.

### 4. RabbitMQ still occupies the host’s default ports

Postgres was moved. 5672 / 25672 / 4369 were not. `docs/warnings.md` mentions this, then README files it under “insignificant, minor warnings.” On a machine that already has a broker — or a shared login node — this is a hard failure, not a tip.

### 5. Shared network plus `trust` / `guest`

The cluster in `data/` uses `pg_hba` `trust` on localhost, RabbitMQ `guest`/`guest`, and Apptainer’s host netns. Any user on the same machine can talk to 5433 and 5672. Fine on a laptop; not fine on KUDPC if that is still the target. The profile password in `~/.aiida/config.json` does not help while `trust` is on.

### 6. Port is baked in on first init only

`port = ${PGPORT:-5433}` is appended only when `.postgresql` does not exist. `AIIDA_POSTGRES_PORT` later changes the client (`PGPORT`) but not the server. Existing `data/` is already on 5433, so this is fine until someone wipes the cluster or overrides the env.

### 7. The README describes a project that is not in the tree

> AiiDA 2.9 + PostgreSQL + RabbitMQ … **and programs to manage scientific calculations.**

The committed README still listed KUDPC (`sp`), QE, and Gaussian. The previous `containerfiles/aiida.def` installed `aiida_plugins/aiida-slurm-rsc`. That directory is empty; the plugin is gone; there is no QE/Gaussian code. `aiida_plugins/`, `quint_models/`, and `.pi` are empty leftovers.

What is here is a working service image. The calculation layer is not started.

## Smaller notes

- `run.sh` does not check that `containerfiles/aiida.sif` exists, so a fresh clone fails inside Apptainer.
- Two `./run.sh` processes share one data dir and will fight over the Postgres lock and RabbitMQ Mnesia dir.
- Bind-mounting the whole `/home/aiida` is what the Docker docs do, but Docker named volumes copy the image home on first use. An empty Apptainer bind hides the image home. `verdi` still comes from conda, so first boot works; extra `pip --user` packages from the image do not. Binding only `.aiida`, `.postgresql`, and `.rabbitmq` is closer to the intent.
- No LICENSE.
- `%environment` `SYSTEM_GID="$(id -g)"` is evaluated when the env script is sourced (runtime). That is what you want; just do not let a future `%post` expand it at build time.

## Suggested order

1. Pin `From: aiidateam/aiida-core-with-services:aiida-2.9.0` and fail the build if the `sed` matches are 0 (or replace `sed` with checked-in scripts).
2. Make the env var name one thing: `AIIDA_DATA_HOME` in both `run.sh` and `docs/warnings.md`.
3. Decide session vs instance in `run.sh` and document it.
4. Treat RabbitMQ port clash as a real constraint; remap it the same way as Postgres if this will run on shared hosts.
5. Restore the Slurm/`sp` plugin and the QE/Gaussian workflow layer, or drop that sentence from the README.
