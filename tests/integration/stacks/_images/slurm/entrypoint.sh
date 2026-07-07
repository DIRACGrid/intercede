#!/bin/bash
set -euo pipefail

ROLE="${1:-controller}"
MUNGE_KEY=/etc/munge/munge.key

wait_for() {
    local desc="$1"; shift
    for _ in $(seq 1 30); do
        "$@" && return 0
        sleep 0.5
    done
    echo "==> timed out waiting for ${desc}" >&2
    return 1
}

start_munge() {
    mkdir -p /var/run/munge

    if [ "$ROLE" = "controller" ]; then
        # Generated fresh per run into the shared munge-key volume - never baked
        # into the image.
        if [ ! -s "$MUNGE_KEY" ]; then
            dd if=/dev/urandom bs=1 count=1024 2>/dev/null > "$MUNGE_KEY"
        fi
    else
        wait_for "munge key from controller" test -s "$MUNGE_KEY"
    fi
    chown munge:munge "$MUNGE_KEY"
    chmod 400 "$MUNGE_KEY"

    chown munge:munge /var/run/munge
    runuser -u munge -- munged
    wait_for "munged socket" test -S /var/run/munge/munge.socket.2
}

case "$ROLE" in
    controller)
        start_munge
        slurmctld
        exec tail -f --retry /var/log/slurm/slurmctld.log
        ;;

    worker)
        start_munge
        slurmd
        exec tail -f --retry /var/log/slurm/slurmd.log
        ;;

    *)
        exec "$@"
        ;;
esac
