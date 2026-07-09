#!/bin/bash
#SBATCH --job-name=array-test
#SBATCH --array=1-4
#SBATCH --ntasks=1
#SBATCH --time=00:01:00
#SBATCH --output=/tmp/slurm-%A_%a.out

echo "Array job $SLURM_ARRAY_JOB_ID task $SLURM_ARRAY_TASK_ID on $(hostname)"
sleep 2
echo "Task $SLURM_ARRAY_TASK_ID done"
