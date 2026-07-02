#!/bin/sh
echo "Running on host: $(hostname)"
echo "Running as user: $(id -un)"
echo "SLURM_JOB_ID=${SLURM_JOB_ID:-unset}"
date
sleep 2
echo "ok $(date -u +%FT%TZ)" > result.txt
