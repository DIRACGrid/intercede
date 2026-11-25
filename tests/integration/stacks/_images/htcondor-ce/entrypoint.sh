#!/bin/bash
# Start the HTCondor pool and the HTCondor-CE in the foreground (no systemd), after writing a
# fresh client token to the shared credentials folder. Exits as soon as either daemon tree stops,
# so `docker compose` and the healthcheck see the failure.
set -euo pipefail

CREDENTIALS="${CREDENTIALS_DIR:-/shared/credentials/htcondor}"
IDENTITY="${CLIENT_IDENTITY:-submituser@users.htcondor.org}"

# The CE has no condor_ce_* wrapper for these two tools: run them with the CE's configuration.
ce() { CONDOR_CONFIG=/etc/condor-ce/condor_config "$@"; }

# The CE signs ID tokens with its pool key. Create a new one at every start: credentials are
# throwaway and never part of the image (integration-images.md, rule 7).
install -d -m 0700 -o root -g root /etc/condor-ce/passwords.d
rm -f /etc/condor-ce/passwords.d/POOL
openssl rand -base64 48 | tr -d '\n' | ce condor_store_cred add -c -i - >/dev/null

# The client's token, readable by everyone: the harness on the host copies it to a private file
# itself (integration-stacks.md, rule 6).
install -d -m 0755 "$CREDENTIALS"
ce condor_token_create -identity "$IDENTITY" > "$CREDENTIALS/token"
chmod 0644 "$CREDENTIALS/token"

condor_master -f &                          # the pool
pool_pid=$!
/usr/share/condor-ce/condor_ce_startup -f & # the CE (checks its config, then runs its master)
ce_pid=$!

# Daemon logs to stdout, so `docker compose logs` holds them all (integration-stacks.md, rule 4).
# HTCondor daemons can only log to files, and cannot open /dev/stdout as that file: they switch
# from root to the condor user, which may not reopen root's stdout. So the files are followed
# instead. `tail -F` waits for files that do not exist yet and prints a header per file.
follow() {
    local tag="$1" dir="$2"; shift 2
    tail -n +1 -F "${@/#/$dir/}" 2>/dev/null | sed -u "s/^/[$tag] /" &
}
follow pool /var/log/condor MasterLog CollectorLog NegotiatorLog SchedLog StartLog \
    SharedPortLog ShadowLog StarterLog.slot1_1 StarterLog.slot1_2 StarterLog.slot1_3 StarterLog.slot1_4
follow ce /var/log/condor-ce MasterLog CollectorLog SchedLog JobRouterLog SharedPortLog AuditLog

trap 'kill "$pool_pid" "$ce_pid" 2>/dev/null; wait' TERM INT
wait -n "$pool_pid" "$ce_pid"
status=$?
echo "a daemon tree stopped (exit $status); stopping the container" >&2
kill "$pool_pid" "$ce_pid" 2>/dev/null || true
exit "$status"
