# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Validate the value of one captured YANG Patch edit in two ways.

Both cases use the router's fixed YANG library and differ only in the
place in the data tree the value is read from. The value is parsed with libyang in
strict, parse-only mode: node names, types and identities are checked,
mandatory nodes and leafref targets are not. That is close to the
anydata-candidate option of draft-netana-netmod-yang-anydata-validation-00.

Exit code 0 means every case gave the expected result.
"""
import json
import sys
import tempfile
from dataclasses import dataclass

import libyang

# The router's fixed YANG library, published with the IETF 127 capture.
# It lists iana-if-type as implemented, so the identity in the value resolves.
FIXED = "library-2338653103.json"

# Where the value is read from.
# ROOT is how collectors and consumers read it today.
# ANCHOR is the parent of the edit's target, as the draft says in section 3.3.
ROOT = None
ANCHOR = "/ietf-interfaces:interfaces"


@dataclass
class Case:
    name: str
    library: str
    read_from: str | None
    expected: str  # "pass" or "fail"


CASES = [
    Case("A", FIXED, ROOT, "fail"),
    Case("B", FIXED, ANCHOR, "pass"),
]


def one_module_set(library: str) -> str:
    """Write the library with the operational schema's module sets merged into one.

    The router builds its operational schema from two module sets,
    config-module and state-module. libyang 6.4.13 reads only the first
    module set of a YANG library (src/context.c, "module-set[1]/module"),
    so without this step it would miss every module of the second set.
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


def validate(value: str, library: str, read_from: str | None) -> tuple[str, str]:
    """Parse the value and return ("pass", "") or ("fail", libyang's error)."""
    with libyang.Context("yang", yanglib_path=one_module_set(library), yanglib_fmt="json") as ctx:
        # The value is parsed as children of this node, or as top-level data.
        parent = ctx.create_data_path(read_from) if read_from else None
        tree = None
        try:
            tree = ctx.parse_data(
                "json",
                libyang.IOType.MEMORY,
                value,
                parent=parent,
                parse_only=True,
                strict=True,
            )
            return "pass", ""
        except libyang.LibyangError as e:
            return "fail", str(e).split(": ", 1)[-1]
        finally:
            # With a parent, the parsed nodes hang below it. Free the whole tree once.
            if parent is not None:
                parent.root().free()
            elif tree is not None:
                tree.root().free()


def main() -> int:
    with open("edit.json", encoding="utf-8") as f:
        edit = json.load(f)
    value = json.dumps(edit["value"])

    print(f"target: {edit['target']}")
    unexpected = 0
    for case in CASES:
        result, error = validate(value, case.library, case.read_from)
        verdict = "ok" if result == case.expected else "UNEXPECTED"
        if result != case.expected:
            unexpected += 1

        anchor = case.read_from or "/"
        print(f"{case.name}  {case.library:24}  anchor {anchor:28}  {result:4}  (expected {case.expected}, {verdict})")
        if error:
            print(f"   {error[:150]}")

    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
