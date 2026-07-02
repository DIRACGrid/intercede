#!/bin/bash
#SBATCH --job-name=hello
#SBATCH --ntasks=1
#SBATCH --time=00:01:00
#SBATCH --output=/tmp/slurm-%j.out

echo "Hello from Slurm job $SLURM_JOB_ID on $(hostname)"
date
sleep 3
echo "Done"
