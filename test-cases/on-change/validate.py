# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Validate the values of two captured YANG Patch edits.

The interface edit (subscription 11) is read from two places: the schema
root, as collectors do today, and the parent of its target, the anchor of
draft-netana-netmod-yang-anydata-validation-00. The alarm edit
(subscription 19) has a top-level target, so its anchor is the root.

The interface target is also resolved to its schema node as XPath, the way
the NetCalyx anchor branch finds the anchor, in three spellings: as the
router sends it, with a quoted key, and in the RFC 8040 encoding that
RFC 8072 prescribes for a YANG Patch target.

Every check runs against two YANG libraries of the router: 2300156437,
the advertised library that every subscription-started names, and
2338653103, the fixed library. libyang parses in strict, parse-only mode: node names, types and identities are
checked, mandatory nodes and leafref targets are not. That is close to the
anydata-candidate option of the draft.

Exit code 0 means every check gave the predicted result.
"""
import json
import os
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


def one_module_set(library: str) -> tuple[str, list[str]]:
    """Write the library in a form libyang loads completely.

    Two changes, both needed only because of how libyang loads a library:

    1. The router builds its operational schema from two module sets,
       config-module and state-module. libyang reads only the first module
       set ("only the first module-set is processed and loaded into the
       context", ly_ctx_new_ylpath in context.h), so the two are merged.
       A module implemented in any set stays implemented.
    2. Deviation modules without a file in yang/ are left out. The library
       lists huawei-ietf-alarms-deviations-NE8000-F1A, but it is in none of
       the receiver caches of the capture repository and not in Huawei's
       YANG repository. They are returned so the output can name them.
    """
    with open(library, encoding="utf-8") as f:
        lib = json.load(f)
    yl = lib["ietf-yang-library:yang-library"]
    schema_name = next(d["schema"] for d in yl["datastore"] if d["name"] == "ietf-datastores:operational")
    set_names = next(s["module-set"] for s in yl["schema"] if s["name"] == schema_name)
    sets = [s for s in yl["module-set"] if s["name"] in set_names]

    implemented = {m["name"]: m for s in sets for m in s.get("module", [])}
    import_only = {m["name"]: m for s in sets for m in s.get("import-only-module", []) if m["name"] not in implemented}

    available = {f.split("@")[0] for f in os.listdir("yang")}
    skipped = []
    for m in implemented.values():
        missing = [d for d in m.get("deviation", []) if d not in available]
        skipped += missing
        if missing:
            m["deviation"] = [d for d in m["deviation"] if d in available]
            if not m["deviation"]:
                del m["deviation"]

    merged = {"name": "operational", "module": list(implemented.values())}
    if import_only:
        merged["import-only-module"] = list(import_only.values())
    yl["module-set"] = [merged]
    yl["schema"] = [{"name": schema_name, "module-set": ["operational"]}]

    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with out:
        json.dump(lib, out)
    return out.name, skipped


def validate(ctx: libyang.Context, value: str, check: dict) -> tuple[str, str]:
    """Parse the value and return ("pass", "") or ("fail", libyang's error)."""
    if check["as_notification"]:
        try:
            ctx.parse_op("json", libyang.IOType.MEMORY, value, dtype=libyang.DataType.NOTIF_YANG)
            return "pass", ""
        except libyang.LibyangError as e:
            return "fail", str(e).split(": ", 1)[-1]

    # The value is parsed as children of this node, or as top-level data.
    parent = ctx.create_data_path(check["anchor"]) if check["anchor"] else None
    tree = None
    try:
        tree = ctx.parse_data("json", libyang.IOType.MEMORY, value, parent=parent, parse_only=True, strict=True)
        return "pass", ""
    except libyang.LibyangError as e:
        return "fail", str(e).split(": ", 1)[-1]
    finally:
        # With a parent, the parsed nodes hang below it. Free the whole tree once.
        if parent is not None:
            parent.root().free()
        elif tree is not None:
            tree.root().free()


def resolve(ctx: libyang.Context, path: str) -> tuple[str, str]:
    """Resolve a target to schema nodes with lys_find_xpath, as NetCalyx does."""
    try:
        nodes = list(ctx.find_path(path))
    except libyang.LibyangError as e:
        return "fail", str(e).split(": ", 1)[-1]
    if len(nodes) != 1:
        return "fail", f"{len(nodes)} schema nodes"
    return "pass", f"schema node {nodes[0].schema_path()}"


def describe(edit: dict) -> str:
    members = ", ".join(f'"{name}"' for name in edit["value"])
    prefixed = all(":" in name for name in edit["value"])
    return f"{{{members}: ...}}  ({'with' if prefixed else 'no'} module prefix)"


def run_checks(ctx: libyang.Context, expected: dict, checks: list[dict]) -> int:
    """Run the checks of one library and return how many did not match."""
    unexpected = 0
    current = None
    for check in checks:
        if check["kind"] == "parse":
            if check["edit"] != current:
                current = check["edit"]
                with open(current, encoding="utf-8") as f:
                    edit = json.load(f)
                value = json.dumps(edit["value"])
                print()
                print(expected["edits"][current])
                print(f"   target: {edit['target']}")
                print(f"   value:  {describe(edit)}")
            result, error = validate(ctx, value, check)
        else:
            if current != "resolve":
                current = "resolve"
                print()
                print("Interface target, resolved as XPath like the NetCalyx anchor branch")
            result, error = resolve(ctx, check["path"])

        v = verdict(check, result, error)
        if v != "ok":
            unexpected += 1
        print(f"{check['id']}  {check['description']:62}  {result:4}  (predicted {check['predicted']}, {v})")
        if check["kind"] == "resolve":
            print(f"   {check['path']}")
        if error:
            print(f"   {error[:150]}")
    return unexpected


def main() -> int:
    expected = load_expected(EXPECTED)
    unexpected = 0
    for library_file, about in expected["libraries"].items():
        checks = [c for c in expected["checks"] if c["library"] == library_file]
        library, skipped = one_module_set(library_file)
        print(f"Library: {library_file}, {about}")
        if skipped:
            print(f"Not found, left out: {', '.join(skipped)}")
        with libyang.Context("yang", yanglib_path=library, yanglib_fmt="json") as ctx:
            unexpected += run_checks(ctx, expected, checks)
        print()
    if unexpected:
        print("Result: at least one check did not match its prediction.")
    else:
        print("Result: the interface value is valid as sent with the fixed library, read from")
        print("        the target's parent. It fails when read from the schema root, and with")
        print("        the advertised library, which lists iana-if-type as import-only.")
        print("        The alarm value cannot pass: its target is a notification, not data,")
        print("        and its alarm-type-id names an identity ietf-alarms does not define,")
        print("        unless the missing deviation changes the type of alarm-type-id.")
        print("        An XPath lookup of the target works only with a quoted key, which")
        print("        neither the router's form nor the RFC 8040 encoding has.")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
