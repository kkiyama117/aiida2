# Warnings and notes

## Session lifetime

`run.py` uses `apptainer run`, which is a foreground session. s6-overlay
brings the services up, runs your command, and tears everything down when it
returns — including the AiiDA daemon. Anything submitted has to finish while
the session is alive.

`apptainer instance start` is not an option: the instance master process is
always PID 1, so s6 never gets it and the services simply do not start (with
a `%startscript` it aborts outright, `s6-overlay-suexec: fatal: can only run
as pid 1`, even with `--no-init`). That is why the definition file has no
`%startscript`, and why there is no real daemon mode here.

`./run.py up` is the closest thing to one: the same `apptainer run` session,
with `sleep infinity` as its command, started with `start_new_session()` so it
outlives the shell that launched it. Its output goes to
`$AIIDA_DATA_HOME/container.log`, and `up` waits for s6's
`legacy-services successfully started` before returning — a more honest
readiness signal than an open port, which on a shared network namespace may
belong to a foreign server. `./run.py down` sends it `SIGTERM`; s6 stops the
daemon, RabbitMQ and PostgreSQL in order, which takes a few seconds.

Nothing supervises that process: it does not survive a reboot and is not
restarted if it dies. It is a session left running, not a service.

## Attaching

`./run.py attach` does *not* enter the running container — Apptainer has no
`docker exec`, and joining the session's PID and mount namespaces would need
privileges. It starts a second container with `apptainer exec`, which skips
the runscript, so s6 never runs there and no service is started or stopped.

It works because of what the two containers share:

- the **host network namespace**, so `verdi` reaches PostgreSQL on
  `localhost:${PGPORT}` and RabbitMQ on 5672 exactly as it does inside the
  session;
- the **bind-mounted state directories**, so it sees the same profile,
  configuration and file repository.

What it does not share is everything in the writable tmpfs, `~/.erlang.cookie`
included — that is regenerated per container, so `rabbitmqctl` and
`rabbitmq-diagnostics` cannot reach the broker from an attached shell. `verdi`
can: it speaks AMQP with `guest`/`guest`. PIDs reported by `verdi daemon`
belong to the session's PID namespace and mean nothing in the attached one.

`attach` deliberately does not take the lock below. It starts no services, so
several attached shells alongside one session are fine.

## Ports

Apptainer shares the host network namespace, so every service in the
container binds host ports.

- **PostgreSQL** was moved to **5433** (`PGPORT`, override with
  `AIIDA_POSTGRES_PORT`) to avoid clashing with a host instance. If something
  else already holds that port, the container does not fall back to it
  silently: the readiness check asks the server on `localhost:${PGPORT}`
  which data directory it serves and refuses anything that is not the
  bundled cluster, so the boot ends with

  ```text
  another server answered there, serving /var/lib/postgres/data; refusing to use it
  ```

  and no AiiDA profile is created. Point `AIIDA_POSTGRES_PORT` at a free
  port and start over with an empty data directory.
- **RabbitMQ** still uses the defaults **5672** (AMQP), **25672** (Erlang
  distribution) and **4369** (epmd). On a host that already runs a broker
  this is a hard failure, not a warning. Remapping it means setting
  `RABBITMQ_NODE_PORT` / `RABBITMQ_DIST_PORT` / `ERL_EPMD_PORT` *and*
  reconfiguring the profile afterwards with
  `verdi profile configure-broker core.rabbitmq --broker-port …`, because
  `verdi presto` has no broker-port option.

The PostgreSQL port is written into `postgresql.conf` only when the cluster
is created, and the profile stores it in `.aiida/config.json`. Changing
`AIIDA_POSTGRES_PORT` afterwards only moves the client and breaks the
profile; it is fixed for the life of the data directory.

## Access control

The cluster is initialised with `initdb` defaults: `trust` authentication for
local connections. RabbitMQ keeps the default `guest`/`guest` user. Combined
with the shared network namespace, any user on the same machine can reach
5433 and 5672. That is acceptable on a personal workstation and not
acceptable on a shared login node.

## State

Everything persistent lives under `data/container/home` (git-ignored), or
`$AIIDA_DATA_HOME` if set:

| path            | contents                          |
| --------------- | --------------------------------- |
| `.aiida`        | profile, config, file repository   |
| `.postgresql`   | PostgreSQL cluster                 |
| `.rabbitmq`     | RabbitMQ mnesia store and logs     |
| `aiida_run`     | work directory of the `localhost` computer |

`/home/aiida` itself is *not* bind-mounted: aiida-core is `pip --user`
installed into the image's `/home/aiida/.local`, and mounting over it would
hide `verdi` entirely. `~/.erlang.cookie` therefore lives in the writable
tmpfs and is regenerated on every start, which is fine for a single-node
broker.

## Host user id

Apptainer ignores the image's `USER` and runs everything as the invoking host
user, but the image ships `/home/aiida` as mode `2770` owned by `1000:1000`,
and `HOME` points there. Any other uid/gid could not even traverse it, so the
definition relaxes that one directory to `0777` at build time — it is the only
path in the image that is not world-readable, and the image is read-only at
runtime anyway. The bundled services do not use `s6-setuidgid`, so no uid
switch is involved and any host user works.

`run.py` takes a `flock` on `$AIIDA_DATA_HOME/.lock` (via `fcntl`, so no
util-linux binary is needed) and leaves the file descriptor open in the
container; two service containers sharing one data directory would corrupt the
PostgreSQL cluster and the mnesia store. The lock lives on the open file
description, so the kernel releases it when the container dies — there is no
stale lock to clear after a crash. The pid written into the file is only what
`status` and `down` report.

## Image patches

`containerfiles/aiida.def` rewrites lines of the upstream init scripts with
`sed`. A `sed` that matches nothing exits 0, so each patch is followed by an
assertion that fails the build.

For the same reason the base image is pinned by **digest**, not by tag: a tag
— even a version tag — can be repushed, and the patched lines would move under
a definition file that has not changed. To move to a newer aiida-core, resolve
the tag yourself and paste the digest into `From:`:

```console
$ TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:aiidateam/aiida-core-with-services:pull" | jq -r .token)
$ curl -sI -H "Authorization: Bearer $TOKEN" \
      -H "Accept: application/vnd.oci.image.index.v1+json" \
      https://registry-1.docker.io/v2/aiidateam/aiida-core-with-services/manifests/<tag> |
  grep -i docker-content-digest
```

Then rebuild and re-check every patch: the assertions catch a `sed` that no
longer matches, but not one that matches the wrong thing.
