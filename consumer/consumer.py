# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT

"""YANG message broker consumer for ClickHouse.

Consumes ietf-telemetry-message records from Kafka, resolves the YANG schema
by the schema-id header, validates each message with libyang and inserts into ClickHouse tables created on first use.

Written from the description in the IETF 127 slides "YANG Message Broker
Consumer Integration Test - ClickHouse Results". It is not that consumer.
"""

import json
import os
import re
from datetime import datetime, timezone

import clickhouse_connect
import libyang
import requests
from _libyang import ffi, lib
from confluent_kafka import Consumer

KAFKA = os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092")
TOPIC = os.environ.get("KAFKA_TOPIC", "telemetry-message-yang")
GROUP = os.environ.get("KAFKA_GROUP", "clickhouse-consumer")
REGISTRY = os.environ.get("SCHEMA_REGISTRY_URL", "http://schema-registry:8081")
CACHE = os.environ.get("SCHEMA_CACHE_DIR", "/var/cache/yang-consumer")
BATCH = int(os.environ.get("BATCH_SIZE", "500"))

MSG = "ietf-telemetry-message:message"
ENVELOPE = "ietf-yp-notification:envelope"
SUBSCRIPTION = "ietf-yang-push-telemetry-message:yang-push-subscription"

# libyang applies strict parsing to anydata content with this flag. The Python
# binding does not name it, so the value is taken from libyang's parser_data.h.
LYD_PARSE_ANYDATA_STRICT = 0x08000000

INT_TYPES = {"int8": "Int8", "int16": "Int16", "int32": "Int32", "int64": "Int64",
             "uint8": "UInt8", "uint16": "UInt16", "uint32": "UInt32", "uint64": "UInt64"}


def ident(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name))


def quote(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def view_name(xpath):
    """/ietf-interfaces:interfaces/interface/statistics -> yang_interfaces_interface_statistics"""
    paths = [re.sub(r"\[[^\]]*\]", "", p.strip()) for p in xpath.split("|")]
    return "yang_" + "__".join(ident(re.sub(r"[^/:]+:", "", p).strip("/")) for p in paths)


def get_json(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


class Schema:
    """The YANG modules behind one schema ID, loaded into a libyang context."""

    def __init__(self, schema_id):
        self.id = schema_id
        self.dir = os.path.join(CACHE, str(schema_id))
        index = os.path.join(self.dir, "modules.json")
        if not os.path.exists(index):
            self._fetch(index)
        with open(index) as f:
            self.modules = json.load(f)
        self.ctx = libyang.Context(self.dir, disable_searchdir_cwd=True)
        for name, features in sorted(self.modules.items()):
            self.ctx.load_module(name, enabled_features=features or None)

    def _fetch(self, index):
        """Fetch the schema and its references, recursively, once per ID."""
        os.makedirs(self.dir, exist_ok=True)
        modules = {}

        def walk(entry):
            name = re.search(r"^\s*(?:sub)?module\s+(\S+)", entry["schema"], re.M).group(1)
            if name in modules:
                return
            tags = (entry.get("metadata") or {}).get("tags") or {}
            modules[name] = tags.get("features", [])
            with open(os.path.join(self.dir, name + ".yang"), "w") as f:
                f.write(entry["schema"])
            for ref in entry.get("references") or []:
                walk(get_json(f"{REGISTRY}/subjects/{ref['subject']}/versions/{ref['version']}"))

        walk(get_json(f"{REGISTRY}/schemas/ids/{self.id}"))
        with open(index, "w") as f:
            json.dump(modules, f)
        print(f"schema {self.id}: fetched {len(modules)} modules from the registry", flush=True)

    def validate(self, value):
        """Return (failed_stage, error). Both are empty for a valid message.

        One strict parse covers the telemetry message, the notification
        envelope, the notification and the pushed data. The NetCalyx reference
        consumer does the same. The stage is read from the path libyang
        reports. Mandatory nodes are not checked, because a push-update holds
        a filtered subset of the datastore.
        """
        tree = ffi.new("struct lyd_node **")
        rc = lib.lyd_parse_data_mem(
            self.ctx.cdata, json.dumps(value).encode(), lib.LYD_JSON,
            lib.LYD_PARSE_STRICT | lib.LYD_PARSE_ONLY | LYD_PARSE_ANYDATA_STRICT, 0, tree)
        if tree[0] != ffi.NULL:
            lib.lyd_free_all(tree[0])
        if not rc:
            return "", ""
        error = str(self.ctx.error("rejected"))
        for marker, stage in (("/datastore-contents", "data"), ("/datastore-changes", "data"),
                              ("/contents/", "notification"), ("/payload/", "envelope")):
            if marker in error:
                return stage, error
        return "message", error


def column_type(leaf):
    t = leaf.type()
    base = t.basename()
    if t.name().split(":")[-1] == "date-and-time":
        return "DateTime64(3, 'UTC')"
    if base in INT_TYPES:
        return INT_TYPES[base]
    if base == "boolean":
        return "Bool"
    if base == "decimal64":
        return "Float64"
    return "String"


class Target:
    """What a subscription's xpath filter selects, as table columns.

    One row is one instance of the innermost list on the path. The key
    columns are the keys of every list on the path. The value columns are
    the selected leaves, or every leaf below the selected node. Nested lists
    below the selected node are left out.
    """

    def __init__(self, schema, xpath):
        self.steps = None     # schema nodes from the root to the row list
        self.keys = []        # (column, depth in steps, key name, ClickHouse type)
        self.columns = {}     # column -> (node names relative to the row list, ClickHouse type)
        for path in xpath.split("|"):
            path = re.sub(r"\[[^\]]*\]", "", path.strip())
            node = next(iter(schema.ctx.find_path(path)))
            chain = [node]
            while chain[0].parent() is not None:
                chain.insert(0, chain[0].parent())
            last_list = max(i for i, n in enumerate(chain) if n.keyword() == "list")
            if self.steps is None:
                self.steps = chain[:last_list + 1]
                for depth, n in enumerate(self.steps):
                    if n.keyword() == "list":
                        for key in n.keys():
                            col = ident(key.name())
                            if any(col == k[0] for k in self.keys):
                                col = ident(f"{n.name()}_{key.name()}")
                            self.keys.append((col, depth, key.name(), column_type(key)))
            self._add_columns([n.name() for n in chain[last_list + 1:]], node)

    def _add_columns(self, rel, node):
        """rel is the path from the row list to node. Columns are named
        relative to node, so a statistics target gives in_octets."""
        if node.keyword() in ("leaf", "leaf-list"):
            self._add(rel, rel[-1:], node)
            return
        skip = {k.name() for k in node.keys()} if node.keyword() == "list" else set()

        def walk(n, path):
            for child in n.children():
                kw = child.keyword()
                if kw in ("leaf", "leaf-list") and not (n is node and child.name() in skip):
                    self._add(rel + path + [child.name()], path + [child.name()], child)
                elif kw == "container":
                    walk(child, path + [child.name()])
        walk(node, [])

    def _add(self, path, name, leaf):
        ctype = column_type(leaf)
        ctype = f"Array({ctype})" if leaf.keyword() == "leaf-list" else f"Nullable({ctype})"
        self.columns[ident("_".join(name))] = (path, ctype)

    def rows(self, contents):
        """Yield one dict per row-list instance found in datastore-contents."""
        def child(obj, name):
            if not isinstance(obj, dict):
                return None
            if name in obj:
                return obj[name]
            for k, v in obj.items():
                if k.split(":")[-1] == name:
                    return v
            return None

        def descend(obj, depth, keys):
            node = self.steps[depth]
            value = child(obj, node.name())
            if value is None:
                return
            for inst in value if node.keyword() == "list" else [value]:
                row = dict(keys)
                for col, d, key, _ in self.keys:
                    if d == depth:
                        row[col] = child(inst, key)
                if depth + 1 < len(self.steps):
                    yield from descend(inst, depth + 1, row)
                    continue
                for col, (path, _) in self.columns.items():
                    v = inst
                    for name in path:
                        v = child(v, name)
                    if v is not None:
                        row[col] = v
                yield row

        yield from descend(contents, 0, {})


MESSAGES_COLUMNS = """
    kafka_timestamp DateTime64(3, 'UTC'),
    kafka_partition UInt32,
    kafka_offset UInt64,
    kafka_key String,
    schema_id UInt32,
    node_export_timestamp Nullable(DateTime64(9, 'UTC')),
    collection_timestamp Nullable(DateTime64(9, 'UTC')),
    notification_event LowCardinality(String),
    session_protocol LowCardinality(String),
    export_address String,
    export_port UInt16,
    collection_address String,
    collection_port UInt16,
    subscription_id UInt32,
    datastore LowCardinality(String),
    xpath_filter String,
    transport LowCardinality(String),
    encoding LowCardinality(String),
    period Nullable(UInt32),
    anchor_time Nullable(DateTime64(3, 'UTC')),
    yang_library_content_id String,
    collector_name LowCardinality(String),
    collector_version LowCardinality(String),
    event_time Nullable(DateTime64(9, 'UTC')),
    hostname String,
    sequence_number Nullable(UInt32),
    notification_type LowCardinality(String),
    notification String,
    valid Bool,
    failed_stage LowCardinality(String),
    error String
"""

SUB_COLUMNS = """
    kafka_timestamp DateTime64(3, 'UTC'),
    kafka_partition UInt32,
    kafka_offset UInt64,
    export_address String,
    hostname String,
    observation_time Nullable(DateTime64(3, 'UTC')),
    sequence_number Nullable(UInt32)"""


class Loader:
    def __init__(self):
        self.ch = clickhouse_connect.get_client(
            host=os.environ.get("CLICKHOUSE_HOST", "clickhouse"),
            port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
            username=os.environ.get("CLICKHOUSE_USER", "yang"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", "yang"),
            database=os.environ.get("CLICKHOUSE_DATABASE", "yang"))
        self.schemas = {}
        self.targets = {}
        self.tables = set()

    def schema(self, schema_id):
        if schema_id not in self.schemas:
            self.schemas[schema_id] = Schema(schema_id)
        return self.schemas[schema_id]

    def stable_view(self, view, comment):
        """Point a view with a stable name at every table that carries comment.

        Table names hold the schema ID and the subscription ID, which change
        between runs and devices. Dashboards query the views instead.
        """
        tables = [r[0] for r in self.ch.query(
            "SELECT name FROM system.tables WHERE database = currentDatabase() "
            "AND engine = 'MergeTree' AND comment = %(c)s ORDER BY name", {"c": comment}).result_rows]
        self.ch.command(f"CREATE OR REPLACE VIEW {view} AS " +
                        " UNION ALL ".join(f"SELECT * FROM {t}" for t in tables))

    def messages_table(self, schema_id):
        table = f"telemetry_schema_{schema_id}_messages"
        if table not in self.tables:
            self.ch.command(
                f"CREATE TABLE IF NOT EXISTS {table} ({MESSAGES_COLUMNS}) ENGINE = MergeTree "
                "ORDER BY (kafka_timestamp, kafka_partition, kafka_offset) COMMENT 'messages'")
            self.tables.add(table)
            self.stable_view("yang_messages", "messages")
        return table

    def sub_table(self, schema, sub_id, xpath):
        table = f"telemetry_schema_{schema.id}_sub_{sub_id}"
        key = (schema.id, sub_id)
        if key not in self.targets:
            target = Target(schema, xpath)
            cols = [f"`{c}` {t}" for c, _, _, t in target.keys]
            cols += [f"`{c}` {t}" for c, (_, t) in target.columns.items()]
            order = ", ".join([f"`{k[0]}`" for k in target.keys] + ["kafka_timestamp"])
            self.ch.command(
                f"CREATE TABLE IF NOT EXISTS {table} ({SUB_COLUMNS},\n    " + ",\n    ".join(cols) +
                f") ENGINE = MergeTree ORDER BY ({order}) COMMENT {quote(xpath)}")
            self.targets[key] = target
            self.stable_view(view_name(xpath), xpath)
            print(f"table {table}: {len(target.keys)} keys, {len(target.columns)} leaf columns for {xpath}",
                  flush=True)
        return table, self.targets[key]

    def process(self, m, out):
        """Add the rows for one Kafka record to out. Return whether it is valid."""
        headers = {k: (v or b"").decode() for k, v in (m.headers() or [])}
        kafka = {
            "kafka_timestamp": datetime.fromtimestamp(m.timestamp()[1] / 1000, timezone.utc).isoformat(),
            "kafka_partition": m.partition(),
            "kafka_offset": m.offset(),
        }
        schema_id = int(headers["schema-id"]) if headers.get("schema-id", "").isdigit() else 0
        row = dict(kafka, kafka_key=(m.key() or b"").decode(errors="replace"), schema_id=schema_id)
        schema, value = None, {}
        try:
            value = json.loads(m.value())
            schema = self.schema(schema_id)
            stage, error = schema.validate(value)
        except Exception as e:  # not JSON, no schema-id header, or the schema does not load
            stage, error = "schema", str(e)
            row["notification"] = (m.value() or b"").decode(errors="replace")
        row.update(valid=not stage, failed_stage=stage, error=error)

        msg = value.get(MSG, {}) if isinstance(value, dict) else {}
        meta = msg.get("telemetry-message-metadata", {})
        sub = meta.get(SUBSCRIPTION, {})
        manifest = msg.get("data-collection-manifest", {})
        envelope = msg.get("payload", {}).get(ENVELOPE, {})
        contents = envelope.get("contents", {})
        kind = next(iter(contents), "")
        body = contents.get(kind, {})
        row.update({
            "node_export_timestamp": meta.get("node-export-timestamp"),
            "collection_timestamp": meta.get("collection-timestamp"),
            "notification_event": meta.get("notification-event", ""),
            "session_protocol": meta.get("session-protocol", ""),
            "export_address": meta.get("export-address", ""),
            "export_port": meta.get("export-port", 0),
            "collection_address": meta.get("collection-address", ""),
            "collection_port": meta.get("collection-port", 0),
            "subscription_id": sub.get("id", body.get("id", 0)),
            "datastore": sub.get("datastore", ""),
            "xpath_filter": sub.get("xpath-filter", ""),
            "transport": sub.get("transport", ""),
            "encoding": sub.get("encoding", ""),
            "period": sub.get("periodic", {}).get("period"),
            "anchor_time": sub.get("periodic", {}).get("anchor-time"),
            "yang_library_content_id": sub.get("yang-library-content-id", ""),
            "collector_name": manifest.get("name", ""),
            "collector_version": manifest.get("software-version", ""),
            "event_time": envelope.get("event-time"),
            "hostname": envelope.get("hostname", ""),
            "sequence_number": envelope.get("sequence-number"),
            "notification_type": kind,
        })
        row.setdefault("notification", json.dumps(contents, separators=(",", ":")))
        out.setdefault(self.messages_table(schema_id), []).append(row)

        data = body.get("datastore-contents")
        if row["valid"] and kind == "ietf-yang-push:push-update" and data and row["xpath_filter"]:
            table, target = self.sub_table(schema, row["subscription_id"], row["xpath_filter"])
            common = dict(kafka, export_address=row["export_address"], hostname=row["hostname"],
                          observation_time=body.get("ietf-yp-observation:timestamp") or row["event_time"],
                          sequence_number=row["sequence_number"])
            for obj in target.rows(data):
                out.setdefault(table, []).append(dict(common, **obj))
        return row["valid"]

    def insert(self, out):
        for table, rows in out.items():
            block = "\n".join(json.dumps(r, separators=(",", ":")) for r in rows).encode()
            self.ch.raw_insert(table, insert_block=block, fmt="JSONEachRow",
                               settings={"date_time_input_format": "best_effort"})


def main():
    loader = Loader()
    consumer = Consumer({
        "bootstrap.servers": KAFKA,
        "group.id": GROUP,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
    consumer.subscribe([TOPIC])
    print(f"consuming {TOPIC} from {KAFKA}, schemas from {REGISTRY}", flush=True)
    total = valid = 0
    while True:
        batch = [m for m in consumer.consume(BATCH, timeout=2.0) if not m.error()]
        if not batch:
            continue
        out = {}
        for m in batch:
            valid += loader.process(m, out)
        loader.insert(out)
        consumer.commit(asynchronous=False)
        total += len(batch)
        rows = sum(len(r) for t, r in out.items() if "_sub_" in t)
        print(f"batch of {len(batch)} messages, {rows} object rows; total {total} consumed, {valid} valid",
              flush=True)


if __name__ == "__main__":
    main()
