#!/bin/bash
# Used by Dockerfile HEALTHCHECK and by the GitLab CI "wait for CE" step.
set -o pipefail

[ -f /run/arc-ready ] || exit 1

sinfo -h >/dev/null 2>&1 || exit 1

curl -sk --max-time 3 -o /dev/null "https://$(hostname)/arex/rest/1.0/info" || exit 1

exit 0
