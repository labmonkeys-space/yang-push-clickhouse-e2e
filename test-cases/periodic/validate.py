# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Validate captured periodic push-updates as a collector and as a consumer.

The collector builds its schema from the router's YANG library: the
modules it finds there for the update, in collector/. The consumer builds
its schema from the registry entry the Schema ID points to, schema 628 of
the NetGauze schema registry, and the schemas its references reach, in
consumer/. Nothing is added on either side, except the text of
iana-if-type, which the library names but no capture holds.

For each check the output shows how the library or the registry closure
lists iana-if-type, and how the libyang context holds it.

Each update is parsed from the schema root with libyang in strict,
parse-only mode: node names, types and identities are checked, mandatory
nodes and leafref targets are not.

Exit code 0 means every check gave the predicted result.
"""
import json
import os
import re
import sys
import tempfile

import libyang

# The checks, their inputs and their predictions. Another implementation
# can run the same checks from this file and compare per check ID.
EXPECTED = "expected-results.json"


def load_expected(path: str) -> dict:
    """Read the checks; a predicted fail must name part of libyang's error."""
    with open(path, encoding="utf-8") as f:
        expected = json.load(f)
    for check in expected["checks"]:
        if check["predicted"] == "fail" and not check.get("error_contains"):
            raise ValueError(f"check {check['id']}: a predicted fail needs error_contains")
    return expected


def verdict(check: dict, result: str, error: str) -> str:
    """ok when the result matches, and a fail fails for the predicted reason."""
    if result != check["predicted"]:
        return "UNEXPECTED"
    if result == "fail" and check["error_contains"] not in error:
        return "UNEXPECTED"
    return "ok"


def one_module_set(library: str) -> str:
    """Write the library with its module sets merged into one.

    The router builds its operational schema from two module sets,
    config-module and state-module. libyang reads only the first module
    set ("only the first module-set is processed and loaded into the
    context", ly_ctx_new_ylpath in context.h), so the two are merged.
    A module implemented in any set stays implemented.
    """
    with open(library, encoding="utf-8") as f:
        lib = json.load(f)
    yl = lib["ietf-yang-library:yang-library"]
    schema_name = next(d["schema"] for d in yl["datastore"] if d["name"] == "ietf-datastores:operational")
    set_names = next(s["module-set"] for s in yl["schema"] if s["name"] == schema_name)
    sets = [s for s in yl["module-set"] if s["name"] in set_names]

    implemented = {m["name"]: m for s in sets for m in s.get("module", [])}
    import_only = {m["name"]: m for s in sets for m in s.get("import-only-module", []) if m["name"] not in implemented}

    merged = {"name": "operational", "module": list(implemented.values())}
    if import_only:
        merged["import-only-module"] = list(import_only.values())
    yl["module-set"] = [merged]
    yl["schema"] = [{"name": schema_name, "module-set": ["operational"]}]

    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with out:
        json.dump(lib, out)
    return out.name


def collector_context(library: str) -> libyang.Context:
    return libyang.Context("collector", yanglib_path=one_module_set(library), yanglib_fmt="json")


def consumer_context() -> libyang.Context:
    """Every module of the registry closure, implemented, with its registered features."""
    with open("consumer/features.json", encoding="utf-8") as f:
        features = json.load(f)
    # Compile once after loading all of them: a when condition may name a
    # module that is loaded later.
    ctx = libyang.Context("consumer", explicit_compile=True)
    for file in sorted(os.listdir("consumer")):
        if not file.endswith(".yang"):
            continue
        with open(os.path.join("consumer", file), encoding="utf-8") as f:
            # A submodule is loaded with the module that includes it.
            if re.search(r"^\s*(sub)?module\s", f.read(), re.M).group(1):
                continue
        name = file[:-5].split("@")[0]
        ctx.load_module(name, enabled_features=features.get(name, []))
    ctx.compile_schema()
    return ctx


def named_revisions(schema: dict) -> dict:
    """The revision the library or the registry closure names for each module."""
    if "registry" in schema:
        return {f[:-5].split("@")[0]: f[:-5].split("@")[1] for f in os.listdir("consumer") if "@" in f}
    with open(schema["library"], encoding="utf-8") as f:
        sets = json.load(f)["ietf-yang-library:yang-library"]["module-set"]
    return {m["name"]: m["revision"] for s in sets for k in ("module", "import-only-module") for m in s.get(k, [])}


def substituted(ctx: libyang.Context, named: dict) -> list[str]:
    """Modules libyang uses at another revision than the one named.

    libyang 6.4.13 builds some base modules into every context, at its own
    revisions. An import without revision-date then resolves to those, not
    to the file at the revision the library or the registry names.
    """
    used = {}
    for m in ctx:
        used.setdefault(m.name(), []).append(next((str(r.date()) for r in m.revisions()), ""))
    return [f"{name} {'/'.join(used[name])} for {rev}" for name, rev in sorted(named.items())
            if name in used and rev not in used[name]]


def listed(schema: dict) -> str:
    """How the library or the registry closure lists iana-if-type."""
    if "registry" in schema:
        return "not in the registry closure"
    with open(schema["library"], encoding="utf-8") as f:
        sets = json.load(f)["ietf-yang-library:yang-library"]["module-set"]
    if any(m["name"] == "iana-if-type" for s in sets for m in s.get("module", [])):
        return "implemented in the library"
    if any(m["name"] == "iana-if-type" for s in sets for m in s.get("import-only-module", [])):
        return "import-only in the library"
    return "not in the library"


def iana_if_type(ctx: libyang.Context) -> str:
    """How the libyang context holds the module that defines the interface types."""
    try:
        return "implemented" if ctx.get_module("iana-if-type").implemented() else "import-only"
    except libyang.LibyangError:
        return "absent"


def validate(ctx: libyang.Context, contents: str, check: dict) -> tuple[str, str]:
    """Parse the update from the schema root and return ("pass", "") or ("fail", libyang's error)."""
    tree = None
    try:
        tree = ctx.parse_data("json", libyang.IOType.MEMORY, contents,
                              parse_only=check["mode"] == "parse-only", strict=True)
        return "pass", ""
    except libyang.LibyangError as e:
        return "fail", str(e).split(": ", 1)[-1]
    finally:
        if tree is not None:
            tree.free()


def main() -> int:
    expected = load_expected(EXPECTED)
    unexpected = 0
    contexts = {}
    current = None
    for check in expected["checks"]:
        if check["payload"] != current:
            current = check["payload"]
            with open(current, encoding="utf-8") as f:
                update = json.load(f)
            contents = json.dumps(update["ietf-yp-notification:envelope"]["contents"]
                                  ["ietf-yang-push:push-update"]["datastore-contents"])
            print()
            print(expected["payloads"][current])

        schema = check["schema"]
        key = schema.get("library") or f"registry {schema['registry']}"
        if key not in contexts:
            contexts[key] = collector_context(schema["library"]) if "library" in schema else consumer_context()
            swapped = substituted(contexts[key], named_revisions(schema))
            if swapped:
                print(f"    {key}: libyang uses {', '.join(swapped)}")
        result, error = validate(contexts[key], contents, check)

        v = verdict(check, result, error)
        if v != "ok":
            unexpected += 1
        print(f"{check['id']}  {check['role']:9}  {check['description']:32}  {result:4}  (predicted {check['predicted']}, {v})")
        print(f"    iana-if-type: {listed(schema)}, {iana_if_type(contexts[key])} in libyang")
        if error:
            print(f"    {error[:150]}")

    for ctx in contexts.values():
        ctx.destroy()

    print()
    if unexpected:
        print("Result: at least one check did not match its prediction.")
    else:
        print("Result: subscription 6, the interfaces.")
        print("        With the advertised library, collector and consumer agree on fail,")
        print("        with the same error. The library lists iana-if-type as import-only,")
        print("        so its identities cannot be used, and schema 628 does not reach it.")
        print("        With the fixed library this collector, which looks up payload prefixes")
        print("        in the library, passes. Schema 628, built by the module-version rule,")
        print("        still fails.")
        print("        Subscription 3, the control: collector and consumer agree on pass.")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
