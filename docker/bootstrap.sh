#!/bin/bash
# =============================================================================
# Runs once at container start (via arc-bootstrap.service, after munge,
# slurmctld and slurmd units). Responsibilities:
#   1. Wait for munge + SLURM to be actually usable
#   2. (Re)generate the ARC Test-CA and a host certificate bound to this
#      container's *runtime* hostname (image build time hostname is random,
#      so we can't bake a valid host cert into the image itself)
#   3. Start arc-arex / arc-arex-ws as configured in /etc/arc.conf
#   4. Mint a Test-CA user certificate for griduser01, which arcctl
#      automatically whitelists in /etc/grid-security/testCA.allowed-subjects
#   5. Wait for the REST endpoint to answer, then signal readiness
# =============================================================================
set -euo pipefail
LOG=/var/log/arc-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

echo "== ARC CE / SLURM bootstrap starting: $(date -u) =="

HOSTNAME_FQDN="$(hostname)"
echo "Using hostname: ${HOSTNAME_FQDN}"

wait_for() {
    local desc="$1"; shift
    local tries=0
    until "$@" >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -gt 90 ]; then
            echo "TIMED OUT waiting for: ${desc}"
            return 1
        fi
        sleep 2
    done
    echo "${desc}: ready (after ${tries} tries)"
}

# --- 1. munge, then SLURM control daemon ------------------------------------
wait_for "munge" bash -c 'echo bootstrap-check | munge | unmunge'
wait_for "slurmctld (sinfo)" sinfo -h

# --- 2. Test-CA + host certificate for the real runtime hostname ------------
arcctl test-ca init -f
arcctl test-ca hostcert -n "${HOSTNAME_FQDN}" -f

# --- 3. Start ARC CE services -------------------------------------------------
arcctl service start --as-configured

# --- 4. Test client certificate for griduser01 -------------------------------
arcctl test-ca usercert --install-user griduser01 -f

# Also export a portable tarball, useful if the GitLab job wants to drive
# arcsub/arcstat/arcget from *outside* this container (e.g. from the
# job's own shell talking to the CE over the docker network).
arcctl test-ca usercert -n griduser01 --export-tar -f || true
mv -f testcert-*.tar.gz /root/arc-test-client.tar.gz 2>/dev/null || true

# --- 5. Wait until the REST endpoint actually answers ------------------------
wait_for "arex REST endpoint" curl -sk "https://${HOSTNAME_FQDN}/arex/rest/1.0/info"

touch /run/arc-ready
echo "== ARC CE / SLURM bootstrap complete: $(date -u) =="
