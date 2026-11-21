# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Copy the entries of the modules the two edits need from a router's YANG library reply into RFC 8525 JSON.

Usage: extract_lib.py <get-yang-library.xml> <out.json>
Keeps the router's module-set, schema and datastore names, features and deviations.
Fails when yang/ has no file at a revision the library names.
"""
import json
import os
import sys
import xml.etree.ElementTree as ET

NS = "{urn:ietf:params:xml:ns:yang:ietf-yang-library}"
WANT = ["ietf-interfaces", "huawei-ietf-interfaces-deviations-NE8000-F1A", "ietf-yang-types", "iana-if-type", "ietf-alarms"]


def txt(e, tag):
    return (e.findtext(NS + tag) or "").strip()


def texts(e, tag):
    return [x.text.strip() for x in e.findall(NS + tag) if x.text and x.text.strip()]


def order(entries):
    return sorted(entries, key=lambda d: WANT.index(d["name"]))


def entry(m):
    d = {"name": txt(m, "name")}
    if txt(m, "revision"):
        d["revision"] = txt(m, "revision")
    d["namespace"] = txt(m, "namespace")
    if texts(m, "feature"):
        d["feature"] = texts(m, "feature")
    if texts(m, "deviation"):
        d["deviation"] = texts(m, "deviation")
    return d


# XML identity values use prefixes bound by xmlns declarations; JSON uses module names.
# For IETF modules the module name is the last part of the namespace URN.
prefixes = {pfx: uri.rsplit(":", 1)[-1] for _, (pfx, uri) in ET.iterparse(sys.argv[1], events=["start-ns"]) if pfx}


def to_json_identity(value):
    pfx, _, name = value.partition(":")
    return f"{prefixes[pfx]}:{name}" if name else value


root = ET.parse(sys.argv[1]).getroot()
yl = root.find(f".//{NS}yang-library")
legacy_root = root.find(f".//{NS}modules-state")

# The schema of the operational datastore, and the module sets it is built from.
op = next(d for d in yl.findall(NS + "datastore") if txt(d, "name").endswith("operational"))
schema_name = txt(op, "schema")
schema = next(s for s in yl.findall(NS + "schema") if txt(s, "name") == schema_name)
sets = {txt(s, "name"): s for s in yl.findall(NS + "module-set")}

out_sets, implemented, import_only = [], [], []
for set_name in texts(schema, "module-set"):
    s = sets[set_name]
    mods = [entry(m) for m in s.findall(NS + "module") if txt(m, "name") in WANT]
    imps = [entry(m) for m in s.findall(NS + "import-only-module") if txt(m, "name") in WANT]
    if not (mods or imps):
        continue
    d = {"name": set_name}
    if mods:
        d["module"] = order(mods)
    if imps:
        d["import-only-module"] = order(imps)
    out_sets.append(d)
    implemented += [x["name"] for x in mods]
    import_only += [x["name"] for x in imps]
assert set(implemented + import_only) == set(WANT), implemented + import_only

# Every module must have its file at the revision the library names,
# or libyang would load a module the router never advertised.
yang_dir = os.path.join(os.path.dirname(os.path.abspath(sys.argv[2])), "yang")
wrong = [f"{m['name']}@{m.get('revision', '')}" for s in out_sets for m in s.get("module", []) + s.get("import-only-module", [])
         if not os.path.exists(os.path.join(yang_dir, f"{m['name']}@{m.get('revision', '')}.yang"))]
if wrong:
    sys.exit(f"no file in yang/ at the revision the library names: {', '.join(wrong)}")

legacy = []
for m in legacy_root.findall(NS + "module"):
    if txt(m, "name") not in WANT:
        continue
    d = {"name": txt(m, "name"), "revision": txt(m, "revision"), "namespace": txt(m, "namespace"),
         "conformance-type": txt(m, "conformance-type")}
    if texts(m, "feature"):
        d["feature"] = texts(m, "feature")
    devs = [{"name": txt(x, "name"), "revision": txt(x, "revision")} for x in m.findall(NS + "deviation")]
    if devs:
        d["deviation"] = devs
    legacy.append(d)

lib = {
    "ietf-yang-library:yang-library": {
        "module-set": out_sets,
        "schema": [{"name": schema_name, "module-set": [d["name"] for d in out_sets]}],
        "datastore": [{"name": to_json_identity(txt(op, "name")), "schema": schema_name}],
        "content-id": txt(yl, "content-id"),
    },
    "ietf-yang-library:modules-state": {"module-set-id": txt(legacy_root, "module-set-id"), "module": order(legacy)},
}
with open(sys.argv[2], "w") as f:
    json.dump(lib, f, indent=2)
    f.write("\n")
print(sys.argv[2], txt(yl, "content-id"), "schema", schema_name, "implemented:", implemented, "import-only:", import_only)
