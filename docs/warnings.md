# Warnings and notes

## Session lifetime

`run.sh` uses `apptainer run`, which is a foreground session. s6-overlay
brings the services up, runs your command, and tears everything down when it
returns — including the AiiDA daemon. Anything submitted has to finish while
the session is alive.

`apptainer instance start` is not an option: the instance master process is
always PID 1, so s6 aborts with `s6-overlay-suexec: fatal: can only run as
pid 1`, even with `--no-init`. That is why the definition file has no
`%startscript`. For unattended runs, keep the session alive yourself, e.g.
under `tmux`, or `nohup ./run.sh verdi run script.py &`.

## Ports

Apptainer shares the host network namespace, so every service in the
container binds host ports.

- **PostgreSQL** was moved to **5433** (`PGPORT`, override with
  `AIIDA_POSTGRES_PORT`) to avoid clashing with a host instance.
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

`run.sh` takes a `flock` on `$AIIDA_DATA_HOME/.lock`; two containers sharing
one data directory would corrupt the PostgreSQL cluster and the mnesia store.

## Image patches

`containerfiles/aiida.def` rewrites lines of the upstream init scripts with
`sed`. A `sed` that matches nothing exits 0, so each patch is followed by an
assertion that fails the build. The base image is pinned to
`aiida-2.9.0` rather than `latest` for the same reason — bumping the tag may
require re-checking the patches.
