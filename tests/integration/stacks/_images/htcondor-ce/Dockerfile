# An HTCondor-CE in front of a one-machine HTCondor pool, for the `htcondor` stack.
# Rules: docs/dev/reference/integration-images.md
#
# The CE is the grid gateway interCEde submits to; its job router hands each job to the local
# pool, which runs it. Both run in this one container, started by entrypoint.sh (no systemd).
FROM almalinux:9

# Shared by every version, so above the ARGs: an ARG is part of the cache key of each later RUN.
# dnf leaves a mirror slower than minrate after `timeout` seconds, instead of finishing on it.
RUN printf '%s\n' 'fastestmirror=True' 'max_parallel_downloads=10' 'minrate=1M' 'timeout=15' \
      >> /etc/dnf/dnf.conf
# The metadata stays cached on purpose: the install below reuses it instead of downloading it again.
RUN set -eux; \
    dnf install -y epel-release dnf-plugins-core; \
    dnf config-manager --set-enabled crb

# Leading edge, updated by Renovate (the `htcondor` group in renovate.json).
# renovate: datasource=github-tags depName=htcondor/htcondor
ARG HTCONDOR_VERSION=v25.14.1
# renovate: datasource=github-tags depName=htcondor/htcondor-ce
ARG HTCONDOR_CE_VERSION=v25.7.0

# Long-term-support anchor, updated by hand: no `# renovate:` comment.
ARG HTCONDOR_LTS_VERSION=25.0.14
ARG HTCONDOR_CE_LTS_VERSION=25.0.1

# Which of the two to build: `latest` or `lts`, from the stack's `versions` (compose passes it).
ARG BACKEND_VERSION=latest

RUN set -eux; \
    case "$BACKEND_VERSION" in \
        latest) condor="${HTCONDOR_VERSION#v}"; ce="${HTCONDOR_CE_VERSION#v}"; series="${condor%%.*}.x" ;; \
        lts)    condor="${HTCONDOR_LTS_VERSION}"; ce="${HTCONDOR_CE_LTS_VERSION}"; series="${condor%%.*}.0" ;; \
        *)      echo "BACKEND_VERSION must be latest or lts, not '$BACKEND_VERSION'" >&2; exit 1 ;; \
    esac; \
    dnf install -y "https://research.cs.wisc.edu/htcondor/repo/${series}/htcondor-release-current.el9.noarch.rpm"; \
    dnf install -y "condor-${condor}" "htcondor-ce-${ce}" "htcondor-ce-condor-${ce}"; \
    dnf clean all; \
    rm -rf /var/cache/dnf

# Jobs run as this unprivileged user: HTCondor refuses to run jobs as root. A token for
# submituser@users.htcondor.org (the CE's trust domain) maps to it.
RUN useradd --create-home submituser

COPY pool.conf /etc/condor/config.d/90-intercede-pool.conf
COPY ce-router.conf /etc/condor-ce/config.d/90-intercede-router.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# The CE's single port (shared by all its daemons).
EXPOSE 9619

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
