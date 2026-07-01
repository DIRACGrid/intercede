#!/bin/bash
# =============================================================================
# Integration test: submit / monitor / retrieve a job through ARC CE, backed
# by SLURM. Meant to run *inside* the arc-ce-slurm container as griduser01
# (that's who the test client cert + queue mapping point to), e.g.:
#
#   docker exec -u griduser01 arc-ce-slurm-test /opt/arc-test/run_integration_test.sh
#
# Exit code 0 = pass, non-zero = fail (so GitLab CI can key off it directly).
# =============================================================================
set -euo pipefail

CE_HOST="$(hostname)"
CE_ENDPOINT="https://${CE_HOST}/arex"
JOB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="$(mktemp -d)"
OUTDIR="${WORKDIR}/output"
POLL_INTERVAL=3
POLL_TIMEOUT=180

log() { echo "[$(date -u +%T)] $*"; }

fail() { log "FAIL: $*"; exit 1; }

trap 'log "cleaning up ${WORKDIR}"; rm -rf "${WORKDIR}"' EXIT

cd "${WORKDIR}"
cp "${JOB_DIR}/job.xrsl" .
cp "${JOB_DIR}/run.sh" .

# -----------------------------------------------------------------------
# 0. Sanity: we need a proxy. arcproxy reads cert/key from ~/.globus by
#    default, which is exactly where `arcctl test-ca usercert --install-user`
#    put them during bootstrap.
# -----------------------------------------------------------------------
log "Generating proxy certificate for $(id -un)"
arcproxy || fail "arcproxy failed - is ~/.globus/usercert.pem present?"

log "Querying CE info endpoint: ${CE_ENDPOINT}"
arcinfo -C "${CE_ENDPOINT}" || fail "arcinfo could not reach ${CE_ENDPOINT}"

# -----------------------------------------------------------------------
# 1. SUBMIT
# -----------------------------------------------------------------------
log "Submitting job.xrsl to ${CE_ENDPOINT}"
SUBMIT_OUTPUT="$(arcsub -C "${CE_ENDPOINT}" job.xrsl 2>&1)" || {
    echo "${SUBMIT_OUTPUT}"
    fail "arcsub did not succeed"
}
echo "${SUBMIT_OUTPUT}"

JOB_ID="$(echo "${SUBMIT_OUTPUT}" | grep -oE 'https://[^ ]+/jobs/[A-Za-z0-9]+' | head -n1)"
[ -n "${JOB_ID}" ] || fail "could not parse job id out of arcsub output"
log "Job submitted: ${JOB_ID}"

# -----------------------------------------------------------------------
# 2. MONITOR
# -----------------------------------------------------------------------
log "Polling job state (timeout ${POLL_TIMEOUT}s)"
elapsed=0
STATE=""
while [ "${elapsed}" -lt "${POLL_TIMEOUT}" ]; do
    STAT_OUTPUT="$(arcstat "${JOB_ID}" 2>&1 || true)"
    STATE="$(echo "${STAT_OUTPUT}" | awk -F': ' '/State:/{print $2; exit}')"
    log "state=${STATE:-unknown}"
    case "${STATE}" in
        Finished|FINISHED)
            break
            ;;
        Failed|FAILED|Killed|KILLED|Deleted)
            echo "${STAT_OUTPUT}"
            fail "job entered terminal failure state: ${STATE}"
            ;;
    esac
    sleep "${POLL_INTERVAL}"
    elapsed=$((elapsed + POLL_INTERVAL))
done

[ "${STATE}" = "Finished" ] || [ "${STATE}" = "FINISHED" ] || {
    arcstat "${JOB_ID}" || true
    arcctl job log "$(basename "${JOB_ID}")" --service || true
    fail "job did not reach Finished state within ${POLL_TIMEOUT}s (last state: ${STATE:-unknown})"
}
log "Job reached Finished state"

# -----------------------------------------------------------------------
# 3. RETRIEVE
# -----------------------------------------------------------------------
mkdir -p "${OUTDIR}"
log "Retrieving output with arcget into ${OUTDIR}"
( cd "${OUTDIR}" && arcget "${JOB_ID}" ) || fail "arcget failed"

RESULT_FILE="$(find "${OUTDIR}" -name result.txt | head -n1)"
STDOUT_FILE="$(find "${OUTDIR}" -name stdout.log | head -n1)"

[ -n "${RESULT_FILE}" ] || fail "result.txt was not retrieved"
[ -n "${STDOUT_FILE}" ] || fail "stdout.log was not retrieved"

grep -q '^ok ' "${RESULT_FILE}" || fail "result.txt did not contain expected content: $(cat "${RESULT_FILE}")"
grep -q 'Running on host' "${STDOUT_FILE}" || fail "stdout.log missing expected marker"

log "Output content:"
cat "${STDOUT_FILE}"
cat "${RESULT_FILE}"

# -----------------------------------------------------------------------
# 4. Cleanup the job from A-REX bookkeeping (not strictly required, but
#    keeps repeated CI runs tidy)
# -----------------------------------------------------------------------
arcclean "${JOB_ID}" || log "warning: arcclean failed (non-fatal)"

log "PASS: submit -> monitor -> retrieve integration test succeeded"
