# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT

# NetCalyx collector: YANG-Push receiver and YANG message broker producer.
# NetCalyx publishes no container image, so this installs the release .deb.
# The .deb is amd64 only. On an arm64 host the image runs under emulation.
# ca-certificates is needed because the schema registry client loads the
# system trust store at start, even for a plain http registry.
# Bump the digest with:
#   gh release view vX.Y.Z -R network-analytics/NetCalyx --json assets \
#     -q '.assets[]|select(.name|endswith(".deb"))|.digest'
FROM --platform=linux/amd64 debian:13-slim

ARG NETCALYX_VERSION=0.4.0
ARG NETCALYX_DEB_SHA256=1ec6dc688bb3244b8b928daf8475b322102af69dc2cf835e74fb2f604b392ab0

ADD --checksum=sha256:${NETCALYX_DEB_SHA256} \
    https://github.com/network-analytics/NetCalyx/releases/download/v${NETCALYX_VERSION}/netcalyx-collector_${NETCALYX_VERSION}-1_amd64.deb \
    /tmp/netcalyx-collector.deb

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates /tmp/netcalyx-collector.deb \
 && rm -rf /var/lib/apt/lists/* /tmp/netcalyx-collector.deb \
 && mkdir -p /var/cache/netcalyx/yang_schemas

# The .deb ships no YANG assets. The YANG producer needs the
# ietf-telemetry-message library to register the envelope schema.
ARG NETCALYX_COMMIT=90e364ec4a08097adf8de14e24361f55a4a2949d
ADD https://github.com/network-analytics/NetCalyx.git#${NETCALYX_COMMIT}:assets/yang/ietf-telemetry-message \
    /opt/netcalyx/assets/yang/ietf-telemetry-message

WORKDIR /opt/netcalyx
COPY config.yaml /etc/netcalyx/config.yaml

ENTRYPOINT ["netcalyx-collector", "/etc/netcalyx/config.yaml"]
