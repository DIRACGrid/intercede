FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

# renovate: datasource=repology depName=ubuntu_26_04/slurm-wlm
ARG SLURM_VERSION=25.11.2-1ubuntu2

RUN apt-get update && \
    apt-get install -y \
        slurmctld=${SLURM_VERSION} \
        slurmd=${SLURM_VERSION} \
        slurm-client=${SLURM_VERSION} \
        munge && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/spool/slurmctld /var/spool/slurmd /var/log/slurm && \
    chown slurm:slurm /var/spool/slurmctld /var/spool/slurmd /var/log/slurm

# The munge package's postinst auto-generates /etc/munge/munge.key on install;
# remove it so the image ships with none. entrypoint.sh generates the real key
# fresh at container start into a shared volume - a key baked into a pushed
# image would be a committed shared secret usable to impersonate any node.
RUN rm -f /etc/munge/munge.key

COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
