# Build-only environment. No GPU, host module installation, or device access.
# Base tag and apt versions are not locked yet; record image ID before use.
FROM debian:13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates git build-essential linux-headers-amd64 \
       python3 zstd xz-utils debhelper dh-sequence-dkms dpkg-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
CMD ["bash"]
