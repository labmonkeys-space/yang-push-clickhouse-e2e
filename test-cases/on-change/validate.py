# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Validate the value of one captured YANG Patch edit in three ways.

Each case picks a YANG library (the schema context) and a place in the
data tree to read the value from. The value is parsed with libyang in
strict, parse-only mode: node names, types and identities are checked,
mandatory nodes and leafref targets are not. That is close to the
anydata-candidate option of draft-netana-netmod-yang-anydata-validation-00.

Exit code 0 means every case gave the expected result.
"""
import json
import sys
from dataclasses import dataclass

import libyang

# The router's library with iana-if-type marked import-only, as advertised,
# and the same library with only that module marked implemented.
AS_ADVERTISED = "library-as-advertised.json"
IANA_IMPLEMENTED = "library-iana-implemented.json"

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
    Case("A", IANA_IMPLEMENTED, ROOT, "fail"),
    Case("B", IANA_IMPLEMENTED, ANCHOR, "pass"),
    Case("C", AS_ADVERTISED, ANCHOR, "fail"),
]


def validate(value: str, library: str, read_from: str | None) -> tuple[str, str]:
    """Parse the value and return ("pass", "") or ("fail", libyang's error)."""
    with libyang.Context("yang", yanglib_path=library, yanglib_fmt="json") as ctx:
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
        print(f"{case.name}  {case.library:30}  anchor {anchor:28}  {result:4}  (expected {case.expected}, {verdict})")
        if error:
            print(f"   {error[:150]}")

    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
