#!/bin/bash
set -euo pipefail

ROLE="${1:-controller}"

start_munge() {
    mkdir -p /var/run/munge
    chown munge:munge /var/run/munge
    runuser -u munge -- munged
    sleep 1
}

case "$ROLE" in
    controller)
        start_munge
        slurmctld
        sleep 2
        echo "==> slurmctld ready"
        sinfo || true
        exec tail -f --retry /var/log/slurm/slurmctld.log
        ;;

    worker)
        start_munge
        slurmd
        sleep 1
        echo "==> slurmd ready on $(hostname)"
        exec tail -f --retry /var/log/slurm/slurmd.log
        ;;

    *)
        exec "$@"
        ;;
esac
