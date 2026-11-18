# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT

# Confluent Schema Registry with the YANG schema provider from
# network-analytics/yang-kafka-integration. The IETF 127 hackathon setup
# mounts the same jar into Confluent Platform 7.8.0. The jar is pinned by digest.
FROM confluentinc/cp-schema-registry:8.3.3

ARG YANG_PLUGIN_VERSION=0.0.5
ARG YANG_PLUGIN_SHA256=3751e9ea59d92e087ee8e390d230aca15d07c6ee8caff0a930b403364b1de5fe

ADD --checksum=sha256:${YANG_PLUGIN_SHA256} --chmod=644 \
    https://github.com/network-analytics/yang-kafka-integration/releases/download/v${YANG_PLUGIN_VERSION}/yang-schema-registry-plugin-${YANG_PLUGIN_VERSION}-shaded.jar \
    /usr/share/java/schema-registry/yang-schema-registry-plugin.jar
