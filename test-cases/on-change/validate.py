# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Validate the values of two captured YANG Patch edits.

The interface edit (subscription 11) is read from two places: the schema
root, as collectors do today, and the parent of its target, the anchor of
draft-netana-netmod-yang-anydata-validation-00. The alarm edit
(subscription 19) has a top-level target, so its anchor is the root.

Every check uses the router's YANG library 2338653103 and parses with
libyang in strict, parse-only mode: node names, types and identities are
checked, mandatory nodes and leafref targets are not. That is close to the
anydata-candidate option of the draft.

Exit code 0 means every check gave the predicted result.
"""
import json
import os
import sys
import tempfile
from dataclasses import dataclass

import libyang

# The router's YANG library, published with the IETF 127 capture.
# It lists iana-if-type as implemented, so the interface identity resolves.
LIBRARY = "library-2338653103.json"


@dataclass
class Edit:
    title: str
    path: str


INTERFACE = Edit("Interface edit, subscription 11", "edit-interface.json")
ALARM = Edit("Alarm edit, subscription 19", "edit-alarm.json")


@dataclass
class Case:
    name: str
    edit: Edit
    description: str
    read_from: str | None  # data node to parse under; None is the schema root
    as_notification: bool  # parse as a YANG notification instead of datastore content
    predicted: str  # "pass" or "fail"


CASES = [
    Case("A", INTERFACE, "read from the schema root, as collectors do today", None, False, "fail"),
    Case("B", INTERFACE, "read from the target's parent, the draft's anchor", "/ietf-interfaces:interfaces", False, "pass"),
    Case("C", ALARM, "read from the schema root, the anchor of a top-level target", None, False, "fail"),
    Case("D", ALARM, "read as a YANG notification instead", None, True, "fail"),
]


def one_module_set(library: str) -> tuple[str, list[str]]:
    """Write the library in a form libyang loads completely.

    Two changes, both needed only because of how libyang loads a library:

    1. The router builds its operational schema from two module sets,
       config-module and state-module. libyang reads only the first module
       set ("only the first module-set is processed and loaded into the
       context", ly_ctx_new_ylpath in context.h), so the two are merged.
       A module implemented in any set stays implemented.
    2. Deviation modules without a file in yang/ are left out, because the
       router does not publish its module files. They are returned so the
       output can name them.
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


def validate(ctx: libyang.Context, value: str, case: Case) -> tuple[str, str]:
    """Parse the value and return ("pass", "") or ("fail", libyang's error)."""
    if case.as_notification:
        try:
            ctx.parse_op("json", libyang.IOType.MEMORY, value, dtype=libyang.DataType.NOTIF_YANG)
            return "pass", ""
        except libyang.LibyangError as e:
            return "fail", str(e).split(": ", 1)[-1]

    # The value is parsed as children of this node, or as top-level data.
    parent = ctx.create_data_path(case.read_from) if case.read_from else None
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


def describe(edit: dict) -> str:
    members = ", ".join(f'"{name}"' for name in edit["value"])
    prefixed = all(":" in name for name in edit["value"])
    return f"{{{members}: ...}}  ({'with' if prefixed else 'no'} module prefix)"


def main() -> int:
    library, skipped = one_module_set(LIBRARY)
    print(f"Library: {LIBRARY} (iana-if-type implemented)")
    if skipped:
        print(f"Not published, left out: {', '.join(skipped)}")

    unexpected = 0
    with libyang.Context("yang", yanglib_path=library, yanglib_fmt="json") as ctx:
        current = None
        for case in CASES:
            if case.edit is not current:
                current = case.edit
                with open(case.edit.path, encoding="utf-8") as f:
                    edit = json.load(f)
                value = json.dumps(edit["value"])
                print()
                print(case.edit.title)
                print(f"   target: {edit['target']}")
                print(f"   value:  {describe(edit)}")

            result, error = validate(ctx, value, case)
            verdict = "ok" if result == case.predicted else "UNEXPECTED"
            if result != case.predicted:
                unexpected += 1
            print(f"{case.name}  {case.description:62}  {result:4}  (predicted {case.predicted}, {verdict})")
            if error:
                print(f"   {error[:150]}")

    print()
    if unexpected:
        print("Result: at least one check did not match its prediction.")
    else:
        print("Result: the interface value is valid as sent. It fails only when read from the schema root.")
        print("        The alarm value cannot pass: its target is a notification, not data,")
        print("        and its alarm-type-id names an identity the library does not define.")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
