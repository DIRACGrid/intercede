# =============================================================================
# ARC CE + SLURM "all-in-one" image for integration testing
#
# Runs, as real systemd-managed services (this is what arcctl/ARC packaging
# expects and is far more robust than re-implementing service supervision):
#   - munge                    (SLURM auth)
#   - slurmctld + slurmd       (single-node SLURM cluster, one fake node)
#   - arc-arex + arc-arex-ws   (NorduGrid ARC CE, LRMS backend = slurm)
#
# Base: AlmaLinux 9. On EL9, ARC7 ships in EPEL as nordugrid-arc7-*.
#
# IMPORTANT: this container runs systemd as PID 1, which needs either
#   docker run --privileged
# or (rootless-friendlier)
#   docker run --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw
# See the .gitlab-ci.yml in this repo for the CI-side flags.
# =============================================================================
FROM almalinux:9

ENV container=docker

# ---------------------------------------------------------------------------
# systemd + EPEL + SLURM + munge + ARC CE + ARC client tools
# ---------------------------------------------------------------------------
RUN dnf -y install epel-release && \
    dnf -y install dnf-plugins-core && \
    dnf config-manager --set-enabled crb && \
    dnf -y update && \
    dnf -y install systemd crypto-policies-scripts && \
    update-crypto-policies --set LEGACY && \
    dnf -y install --setopt=strict=1 \
        munge munge-libs \
        slurm slurm-slurmctld slurm-slurmd \
        openssl ca-certificates \
        procps-ng iproute net-tools which curl jq \
        && \
    dnf -y install --setopt=strict=1 \
        nordugrid-arc7-arex \
        nordugrid-arc7-client \
        nordugrid-arc7-arcctl \
        && \
    dnf clean all && \
    # Hard assertion: fail the build loudly right here if anything above
    # was silently skipped, instead of finding out at container runtime.
    rpm -q munge munge-libs slurm slurm-slurmctld slurm-slurmd \
           nordugrid-arc7-arex nordugrid-arc7-client nordugrid-arc7-arcctl && \
    # standard "systemd in docker" cleanup: mask units that don't apply / fail in containers
    (cd /lib/systemd/system/sysinit.target.wants/; for i in *; do [ "$i" = systemd-tmpfiles-setup.service ] || rm -f "$i"; done) ; \
    rm -f /lib/systemd/system/multi-user.target.wants/* ; \
    rm -f /etc/systemd/system/*.wants/* ; \
    rm -f /lib/systemd/system/local-fs.target.wants/* ; \
    rm -f /lib/systemd/system/sockets.target.wants/*udev* ; \
    rm -f /lib/systemd/system/sockets.target.wants/*initctl* ; \
    rm -f /lib/systemd/system/basic.target.wants/* ; \
    rm -f /lib/systemd/system/anaconda.target.wants/* 2>/dev/null || true

# ---------------------------------------------------------------------------
# slurm service account (EPEL's slurm packages do NOT create this user
# automatically, unlike some other distros' packaging - has to be done here)
# ---------------------------------------------------------------------------
RUN groupadd -r slurm --gid=990 && \
    useradd -r -c "SLURM workload manager" -d /var/lib/slurm -u 990 -g slurm -s /sbin/nologin slurm && \
    mkdir -p /var/lib/slurm && chown slurm:slurm /var/lib/slurm

# ---------------------------------------------------------------------------
# Unprivileged pool account that grid jobs get mapped to + matching SLURM node
# ---------------------------------------------------------------------------
RUN useradd -m -s /bin/bash griduser01

# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------
COPY slurm.conf   /etc/slurm/slurm.conf
COPY cgroup.conf  /etc/slurm/cgroup.conf
COPY arc.conf     /etc/arc.conf

COPY bootstrap.sh          /usr/local/bin/bootstrap.sh
COPY healthcheck.sh        /usr/local/bin/healthcheck.sh
COPY arc-bootstrap.service /etc/systemd/system/arc-bootstrap.service

# Debug aid: prints exactly which systemd unit names the munge/slurm
# packages actually shipped. Check this in the build log if bootstrap.sh
# ever times out waiting for munge/slurmctld/slurmd at runtime.
# Debug aid (non-fatal): shows exactly what's installed for munge/slurm
# and whether they shipped systemd units. Read this in the build log if
# bootstrap.sh ever times out waiting for munge/slurmctld/slurmd later.
RUN set +e; \
    echo "== rpm -q (are the packages even installed?) =="; \
    rpm -q munge slurm slurm-slurmctld slurm-slurmd; \
    echo "== munge file list =="; rpm -ql munge 2>&1; \
    echo "== slurm file list =="; rpm -ql slurm 2>&1; \
    echo "== slurm-slurmctld file list =="; rpm -ql slurm-slurmctld 2>&1; \
    echo "== slurm-slurmd file list =="; rpm -ql slurm-slurmd 2>&1; \
    echo "== anything under /usr/lib/systemd/system matching munge or slurm =="; \
    ls -la /usr/lib/systemd/system/ | grep -iE 'munge|slurm'; \
    true

RUN mkdir -p /var/spool/slurmctld /var/spool/slurmd /var/log/slurm && \
    chown slurm:slurm /var/spool/slurmctld /var/spool/slurmd /var/log/slurm && \
    chmod +x /usr/local/bin/bootstrap.sh /usr/local/bin/healthcheck.sh && \
    systemctl enable arc-bootstrap.service
# NOTE: munge.service / slurmctld.service / slurmd.service are deliberately
# NOT enabled here. arc-bootstrap.service declares Wants=/After= on all
# three, so systemd pulls them in automatically as dependencies when
# arc-bootstrap.service starts at boot - no need to double-enable them,
# and it avoids brittle unit-name lookups at build time.

EXPOSE 443

HEALTHCHECK --interval=5s --timeout=5s --start-period=90s --retries=30 \
    CMD /usr/local/bin/healthcheck.sh

STOPSIGNAL SIGRTMIN+3
CMD ["/usr/sbin/init"]
