#!/bin/bash
#SBATCH --job-name=collatz
#SBATCH --ntasks=1
#SBATCH --time=00:01:00
#SBATCH --output=/tmp/slurm-%j.out

start=$(( SLURM_JOB_ID % 1000 ))
# Avoid starting at 0
[ "$start" -eq 0 ] && start=1000

echo "Job $SLURM_JOB_ID: Collatz sequence from $start"

n=$start
steps=0
sequence="$n"

while [ "$n" -ne 1 ]; do
    if [ $(( n % 2 )) -eq 0 ]; then
        n=$(( n / 2 ))
    else
        n=$(( n * 3 + 1 ))
    fi
    steps=$(( steps + 1 ))
    sequence="$sequence → $n"
done

echo "Sequence: $sequence"
echo "Steps: $steps"
