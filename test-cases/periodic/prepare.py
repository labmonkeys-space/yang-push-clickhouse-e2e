# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Cut the inputs of the periodic case from the capture repository.

Usage: prepare.py <capture-dir> <case-dir>

<capture-dir> holds the capture files at their paths in
network-analytics/ietf-network-analytics-document-status. The script writes
the two push-updates, the two libraries, collector/, consumer/ and
SOURCES.md into <case-dir>. It is deterministic.

Collector side: the modules a receiver finds through the router's YANG
library for one push-update. Those are the modules of module-version, the
modules whose names prefix a member or an identity value in the payload,
their imports, includes and the deviations the library lists, at the
revisions the library names. The texts come from the NetCalyx receiver
cache, except iana-if-type, whose text no capture holds.

Consumer side: schema 628 of the NetGauze schema registry and every schema
its references reach, resolved by subject and version through the
registrations in the NetGauze collector log. Nothing else.
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import xml.etree.ElementTree as ET

RUN = "127/Hackathon/mb-alive-test-result"
RECEIVER = f"{RUN}/netcalyx/netcalyx-yangpush-receiver"
PAYLOADS = {
    "push-update-s06.json": f"{RECEIVER}/yp-s06-push-update.json",
    "push-update-s03.json": f"{RECEIVER}/yp-s03-push-update.json",
}
STARTED = {
    "push-update-s06.json": f"{RECEIVER}/yp-s06-subscription-started.json",
    "push-update-s03.json": f"{RECEIVER}/yp-s03-subscription-started.json",
}
LIBRARY_XML = "Hackathon/yangpush-publisher/huawei-vrp-ip/nc-get-reply/get-yang-library.xml"
LIBRARIES = {
    "2300156437": (f"126/{LIBRARY_XML}", ["push-update-s06.json", "push-update-s03.json"]),
    "2338653103": (f"127/{LIBRARY_XML}", ["push-update-s06.json"]),
}
RECEIVER_CACHE = f"{RECEIVER}/alive-netcalyx-receiver-cache-content.tar.gz"
CACHE_CONTENT_ID = "453f4d25a4ebb5a8404c1121382e85d142acfe603df24b7957a1a6f8291adddb"
REGISTRY = f"{RUN}/netgauze/yang-schema-registry/alive-netgauze-confluent-schemaregistry-schema-content.tar.gz"
COLLECTOR_LOG = f"{RUN}/netgauze/netgauze-yangpush-receiver/alive-netgauze-receiver-log.tar.gz"
SCHEMA_ID = 628

# The one module text no capture holds. The library names it at this revision.
NOT_CAPTURED = {"iana-if-type@2017-01-19": "../on-change/yang/iana-if-type@2017-01-19.yang"}

NS = "{urn:ietf:params:xml:ns:yang:ietf-yang-library}"
HOSTNAME = re.compile(r"ipf-zbl\d+-r-[a-z]+-\d+")
PREFIX = re.compile(r"^([a-z][a-z0-9-]*):[A-Za-z]")


def fail(message):
    sys.exit(f"prepare.py: {message}")


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def replace_hostnames(text):
    """The router is router-a, every other host router-b, router-c, ... in sorted order."""
    names = sorted(set(HOSTNAME.findall(text)))
    own = json.loads(text)["ietf-yp-notification:envelope"]["hostname"]
    others = [n for n in names if n != own]
    mapping = {own: "router-a", **{n: f"router-{chr(ord('b') + i)}" for i, n in enumerate(others)}}
    return HOSTNAME.sub(lambda m: mapping[m.group(0)], text)


def payload_prefixes(node):
    """Module names that prefix a member name or an identity value."""
    found = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if ":" in k:
                found.add(k.split(":")[0])
            found |= payload_prefixes(v)
    elif isinstance(node, list):
        for v in node:
            found |= payload_prefixes(v)
    elif isinstance(node, str):
        m = PREFIX.match(node)
        if m:
            found.add(m.group(1))
    return found


def module_version(started_path):
    with open(started_path, encoding="utf-8") as f:
        started = json.load(f)
    s = started["ietf-yp-notification:envelope"]["contents"]["ietf-subscribed-notifications:subscription-started"]
    return {m["name"]: m["revision"] for m in s["ietf-yang-push-revision:module-version"]}


class Library:
    """The operational schema of a router's YANG library reply."""

    def __init__(self, path):
        root = ET.parse(path).getroot()
        self.prefixes = {pfx: uri.rsplit(":", 1)[-1] for _, (pfx, uri) in ET.iterparse(path, events=["start-ns"]) if pfx}
        yl = root.find(f".//{NS}yang-library")
        self.content_id = yl.findtext(NS + "content-id").strip()
        op = next(d for d in yl.findall(NS + "datastore") if d.findtext(NS + "name").strip().endswith("operational"))
        self.datastore = self.json_identity(op.findtext(NS + "name").strip())
        self.schema = op.findtext(NS + "schema").strip()
        schema = next(s for s in yl.findall(NS + "schema") if s.findtext(NS + "name").strip() == self.schema)
        set_names = [x.text.strip() for x in schema.findall(NS + "module-set")]
        sets = {s.findtext(NS + "name").strip(): s for s in yl.findall(NS + "module-set")}
        self.sets = []  # (set name, [(conformance, entry)])
        self.entries = {}  # name -> entry, implemented wins over import-only
        for name in set_names:
            members = []
            for tag in ("module", "import-only-module"):
                for m in sets[name].findall(NS + tag):
                    e = self.entry(m)
                    members.append((tag, e))
                    if tag == "module" or e["name"] not in self.entries:
                        self.entries[e["name"]] = e
            self.sets.append((name, members))

    def json_identity(self, value):
        pfx, _, name = value.partition(":")
        return f"{self.prefixes[pfx]}:{name}" if name else value

    @staticmethod
    def entry(m):
        def texts(tag):
            return [x.text.strip() for x in m.findall(NS + tag) if x.text and x.text.strip()]

        d = {"name": m.findtext(NS + "name").strip()}
        if (m.findtext(NS + "revision") or "").strip():
            d["revision"] = m.findtext(NS + "revision").strip()
        d["namespace"] = m.findtext(NS + "namespace").strip()
        subs = [{"name": s.findtext(NS + "name").strip(), "revision": s.findtext(NS + "revision").strip()}
                for s in m.findall(NS + "submodule")]
        if subs:
            d["submodule"] = subs
        if texts("feature"):
            d["feature"] = texts("feature")
        if texts("deviation"):
            d["deviation"] = texts("deviation")
        return d

    def cut(self, names):
        """The library in RFC 8525 JSON, reduced to these modules."""
        out_sets = []
        for set_name, members in self.sets:
            d = {"name": set_name}
            for tag in ("module", "import-only-module"):
                kept = sorted((e for t, e in members if t == tag and e["name"] in names), key=lambda e: e["name"])
                if kept:
                    d[tag] = kept
            if len(d) > 1:
                out_sets.append(d)
        return {"ietf-yang-library:yang-library": {
            "module-set": out_sets,
            "schema": [{"name": self.schema, "module-set": [d["name"] for d in out_sets]}],
            "datastore": [{"name": self.datastore, "schema": self.schema}],
            "content-id": self.content_id,
        }}


def collector_modules(library, seeds, texts):
    """Close the seed modules over imports, includes and deviations, at library revisions."""
    files, names, todo = {}, set(), sorted(seeds)
    while todo:
        name = todo.pop()
        if name in names:
            continue
        if name not in library.entries:
            fail(f"library {library.content_id} does not list {name}")
        names.add(name)
        e = library.entries[name]
        units = [(name, e.get("revision", ""))] + [(s["name"], s["revision"]) for s in e.get("submodule", [])]
        for unit, rev in units:
            key = f"{unit}@{rev}"
            if key not in texts:
                fail(f"no text of {key}, the revision library {library.content_id} names")
            files[key] = texts[key]
            body = texts[key].decode("utf-8")
            todo += re.findall(r"^\s*import\s+([\w.-]+)", body, re.M)
        todo += e.get("deviation", [])
    return names, files


def read_tar(path):
    with tarfile.open(path) as t:
        return {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}


def receiver_texts(capture):
    texts = {}
    for name, data in read_tar(os.path.join(capture, RECEIVER_CACHE)).items():
        if name.startswith(f"{CACHE_CONTENT_ID}/modules/") and name.endswith(".yang"):
            texts[os.path.basename(name)[:-5]] = data
    for key, path in NOT_CAPTURED.items():
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), path), "rb") as f:
            texts[key] = f.read()
    return texts


def registry_closure(capture):
    """Schema 628 and every schema its references reach, keyed by module name."""
    schemas = {}
    for name, data in read_tar(os.path.join(capture, REGISTRY)).items():
        m = re.search(r"schema_id_(\d+)\.json$", name)
        if m:
            schemas[int(m.group(1))] = json.loads(data)
    log = read_tar(os.path.join(capture, COLLECTOR_LOG))["collector.log"].decode("utf-8")
    log = re.sub(r"\x1b\[[0-9;]*m", "", log)
    ids = {(s, int(v)): int(i) for s, v, i in re.findall(
        r'registered schema \S+ with subject `Some\("([^"]+)"\)`, version `Some\((\d+)\)` and ID `Some\((\d+)\)`', log)}

    closure, seen, todo = {}, set(), [SCHEMA_ID]
    root = None
    while todo:
        sid = todo.pop()
        if sid in seen:
            continue
        seen.add(sid)
        schema = schemas[sid]
        module = re.search(r"^\s*(?:sub)?module\s+([\w.-]+)", schema["schema"], re.M).group(1)
        root = root or module
        if module in closure:
            if module == root:
                continue  # the root wins when a cycle reaches another version of it
            fail(f"schema {sid} and schema {closure[module][0]} both define {module}")
        closure[module] = (sid, schema)
        for ref in schema.get("references", []):
            key = (ref["subject"], ref["version"])
            if key not in ids:
                fail(f"no registration of subject {ref['subject']} version {ref['version']} in the collector log")
            todo.append(ids[key])
    return closure


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    capture, case = sys.argv[1], sys.argv[2]
    sources = []

    # The two push-updates, hostnames replaced.
    seeds = {}
    for out, src in PAYLOADS.items():
        path = os.path.join(capture, src)
        with open(path, encoding="utf-8") as f:
            text = replace_hostnames(f.read())
        doc = json.loads(text)
        with open(os.path.join(case, out), "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")
        contents = doc["ietf-yp-notification:envelope"]["contents"]["ietf-yang-push:push-update"]["datastore-contents"]
        seeds[out] = (set(module_version(os.path.join(capture, STARTED[out]))), payload_prefixes(contents))
        sources += [(out, src, sha256(path)), (out, STARTED[out], sha256(os.path.join(capture, STARTED[out])))]

    # Collector side: one library per content-id, cut to the modules its checks need.
    texts = receiver_texts(capture)
    collector = os.path.join(case, "collector")
    shutil.rmtree(collector, ignore_errors=True)
    os.makedirs(collector)
    for content_id, (src, payloads) in LIBRARIES.items():
        path = os.path.join(capture, src)
        library = Library(path)
        if library.content_id != content_id:
            fail(f"{src} has content-id {library.content_id}, expected {content_id}")
        names, files = set(), {}
        for p in payloads:
            # A payload prefix counts only when the library names a module of that name.
            named, prefixes = seeds[p]
            n, f = collector_modules(library, named | {x for x in prefixes if x in library.entries}, texts)
            names |= n
            files |= f
        for key, data in files.items():
            with open(os.path.join(collector, f"{key}.yang"), "wb") as f:
                f.write(data)
        out = f"library-{content_id}.json"
        with open(os.path.join(case, out), "w", encoding="utf-8") as f:
            json.dump(library.cut(names), f, indent=2)
            f.write("\n")
        sources.append((out, src, sha256(path)))
        print(f"{out}: {len(names)} modules")
    sources.append(("collector/", RECEIVER_CACHE, sha256(os.path.join(capture, RECEIVER_CACHE))))

    # Consumer side: schema 628 and its closure, nothing else.
    closure = registry_closure(capture)
    for required in ("ietf-interfaces", "huawei-devm"):
        if required not in closure:
            fail(f"the closure of schema {SCHEMA_ID} has no {required}")
    if "iana-if-type" in closure:
        fail(f"the closure of schema {SCHEMA_ID} has iana-if-type, so check P3 would not test the gap")
    consumer = os.path.join(case, "consumer")
    shutil.rmtree(consumer, ignore_errors=True)
    os.makedirs(consumer)
    features = {}
    for module, (sid, schema) in sorted(closure.items()):
        rev = re.search(r"^\s*revision\s+\"?([\d-]+)", schema["schema"], re.M)
        name = f"{module}@{rev.group(1)}" if rev else module
        with open(os.path.join(consumer, f"{name}.yang"), "w", encoding="utf-8") as f:
            f.write(schema["schema"])
        feats = (schema.get("metadata") or {}).get("tags", {}).get("features", [])
        if feats:
            features[module] = sorted(feats)
    with open(os.path.join(consumer, "features.json"), "w", encoding="utf-8") as f:
        json.dump(features, f, indent=2, sort_keys=True)
        f.write("\n")
    sources += [("consumer/", REGISTRY, sha256(os.path.join(capture, REGISTRY))),
                ("consumer/", COLLECTOR_LOG, sha256(os.path.join(capture, COLLECTOR_LOG)))]
    print(f"consumer/: {len(closure)} modules from schema {SCHEMA_ID}")

    write_sources(case, sources)


def write_sources(case, sources):
    lines = [
        "# Where the inputs come from",
        "",
        "Written by `prepare.py`.",
        "Capture paths are relative to the root of [network-analytics/ietf-network-analytics-document-status](https://github.com/network-analytics/ietf-network-analytics-document-status).",
        "",
        "| Input | Capture file | SHA-256 of the capture file |",
        "| --- | --- | --- |",
    ]
    lines += [f"| `{out}` | `{src}` | `{digest}` |" for out, src, digest in sources]
    for key in NOT_CAPTURED:
        lines.append(f"| `collector/{key}.yang` | named by the library, text not captured | the IANA module at that revision, as nl6 ships it |")
    with open(os.path.join(case, "SOURCES.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
