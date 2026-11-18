# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT

"""Follow the message count through the chain and print the result.

Four counts are compared: messages nl6 sent, messages NetCalyx decoded,
records produced to Kafka and rows in ClickHouse. They are totals for the
whole stack since it started, not per device. Per-device reconciliation needs
labmonkeys-space/nl6#1061.

Exits 1 when the four counts differ, when no message is valid, or when a
message failed for a reason other than the ones listed in EXPECTED_INVALID.
"""

import os
import re
import sys
import time

import clickhouse_connect
import requests
from confluent_kafka import Consumer, TopicPartition

TOPIC = os.environ.get("KAFKA_TOPIC", "telemetry-message-yang")

# Known upstream findings, labmonkeys-space/nl6#1017. The captured NE 8000
# sends both shapes too.
EXPECTED_INVALID = [
    # The registered schema lacks iana-if-type, so interface type identities
    # cannot be resolved.
    'Invalid identityref "iana-if-type:',
    # The YANG Patch value of an on-change update is rooted at the list, which
    # is not a top-level node.
    'Node "interface" not found in the "ietf-interfaces" module',
    # The same finding with the patch value a real VRP router sends, which has
    # no module prefix on its root node. See labmonkeys-space/nl6#1062.
    'Node "interface" not found in the "ietf-yang-push" module',
]

NL6 = os.environ.get("NL6_API", "http://nl6:8080")
METRICS = os.environ.get("NETCALYX_METRICS", "http://otel:8889/metrics")

consumer = Consumer({"bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092"), "group.id": "report"})
ch = clickhouse_connect.get_client(host="clickhouse", username="yang", password="yang", database="yang")


def metric(text, name):
    """Sum a NetCalyx counter over all its label sets."""
    return int(sum(float(v) for v in re.findall(rf"^{name}(?:_total)?(?:{{.*}})? (\S+)$", text, re.M)))


def snapshot():
    status = requests.get(f"{NL6}/api/v1/yangpush/status", timeout=10).json()
    sent = sum(c["messages_sent"] for c in status["collectors"])
    failures = sum(c["send_failures"] for c in status["collectors"])
    text = requests.get(METRICS, timeout=10).text
    decoded = metric(text, "netcalyx_udp_notif_decoder_decoded")
    produced = 0
    for p in consumer.list_topics(TOPIC, timeout=10).topics[TOPIC].partitions:
        low, high = consumer.get_watermark_offsets(TopicPartition(TOPIC, p), timeout=10)
        produced += high - low
    rows = ch.query(
        "SELECT schema_id, subscription_id, uniqExact(export_address), count(), countIf(valid), "
        f"countIf(NOT valid AND NOT multiSearchAny(error, {EXPECTED_INVALID!r})), any(xpath_filter), "
        "anyIf(failed_stage, NOT valid), anyIf(error, NOT valid) FROM yang_messages GROUP BY 1, 2 ORDER BY 1, 2"
    ).result_rows if ch.query("EXISTS VIEW yang_messages").result_rows[0][0] else []
    return sent, failures, decoded, produced, rows


# The devices keep sending and NetCalyx exports its counters every 10 s, so
# take snapshots until the chain is quiet and the four counts agree.
for attempt in range(8):
    sent, failures, decoded, produced, rows = snapshot()
    consumed = sum(r[3] for r in rows)
    if sent == decoded == produced == consumed:
        break
    time.sleep(5)
consumer.close()

valid = sum(r[4] for r in rows)
unexpected = sum(r[5] for r in rows)
complete = sent == decoded == produced == consumed and failures == 0

print(f"Messages sent by nl6:         {sent}" + (f"  ({failures} send failures)" if failures else ""))
print(f"Messages decoded by NetCalyx: {decoded}")
print(f"Messages produced to Kafka:   {produced}")
print(f"Messages in ClickHouse:       {consumed}")
print(f"Messages valid:               {valid}")
print(f"Invalid, known finding:       {consumed - valid - unexpected}")
print(f"Invalid, unexpected:          {unexpected}")
print()
print(f"{'Schema':>6} {'Sub':>4} {'Devices':>7} {'Messages':>8} {'Valid':>6} {'Rows':>8}  Xpath")
for schema, sub, devices, count, ok, _, xpath, stage, error in rows:
    table = f"telemetry_schema_{schema}_sub_{sub}"
    objects = ch.query(f"SELECT count() FROM {table}").result_rows[0][0] if ch.query(
        f"EXISTS TABLE {table}").result_rows[0][0] else 0
    print(f"{schema:>6} {sub:>4} {devices:>7} {count:>8} {ok:>6} {objects:>8}  {xpath}")
    if ok < count:
        print(f"{'':>45}  fails at stage '{stage}': {error[:150]}")

failed = not complete or valid == 0 or unexpected > 0
print()
if not complete:
    print("The four message counts differ. The first count that drops shows where messages are lost.")
print("FAILED" if failed else "PASSED: every message nl6 sent is in ClickHouse")
sys.exit(1 if failed else 0)
