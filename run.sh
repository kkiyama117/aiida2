#!/bin/sh
# Start the AiiDA container.  s6-overlay supervises PostgreSQL, RabbitMQ and
# the AiiDA daemon, so it has to be PID 1 (--pid --no-init) and needs a
# writable /run (--writable-tmpfs).
#
# This is an interactive session, not a service: when the command exits, s6
# stops the daemon and both services.  Long-running workflows do not survive
# it -- see docs/warnings.md.
set -e

DIR=$(cd "$(dirname "$0")" && pwd)
SIF=$DIR/containerfiles/aiida.sif
DATA=${AIIDA_DATA_HOME:-$DIR/data/container/home}

if [ ! -f "$SIF" ]; then
    echo "$SIF not found.  Build it with:" >&2
    echo "  apptainer build --fakeroot $SIF $DIR/containerfiles/aiida.def" >&2
    exit 1
fi

# Only the state directories are bind-mounted.  Mounting the whole
# /home/aiida would hide the image's own home, and aiida-core lives there:
# it is pip --user installed into /home/aiida/.local.
mkdir -p "$DATA/.aiida" "$DATA/.postgresql" "$DATA/.rabbitmq" "$DATA/aiida_run"

# One container per data directory; two would corrupt the PostgreSQL cluster
# and the RabbitMQ mnesia store.  This is a data-safety guard, so a missing
# flock is a hard error rather than a silently skipped lock.
if ! command -v flock > /dev/null 2>&1; then
    echo "flock not found; it is required to keep two containers from" >&2
    echo "sharing $DATA.  Install it (util-linux) and try again." >&2
    exit 1
fi
exec 9> "$DATA/.lock"
flock -n 9 || {
    echo "another container is already using $DATA" >&2
    exit 1
}

exec apptainer run --pid --no-init --writable-tmpfs \
    -B "$DATA/.aiida":/home/aiida/.aiida \
    -B "$DATA/.postgresql":/home/aiida/.postgresql \
    -B "$DATA/.rabbitmq":/home/aiida/.rabbitmq \
    -B "$DATA/aiida_run":/home/aiida/aiida_run \
    "$SIF" "$@"
