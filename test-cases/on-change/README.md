# Test case: the value of a captured on-change edit

## Problem statement

A Huawei NE8000 sends on-change updates as YANG Patch edits inside a `push-change-update` ([RFC 8641](https://www.rfc-editor.org/rfc/rfc8641), [RFC 8072](https://www.rfc-editor.org/rfc/rfc8072)).
Each edit has a `target`, here one interface entry, and a `value` that holds only that entry, not a tree from the schema root.
Collectors and consumers validate the value from the schema root and reject it.
The question is what the value should be validated against, and which payload should pass.

## Evidence

The edit, from subscription 11 of the IETF 127 capture, shortened:

```json
"target": "/ietf-interfaces:interfaces/interface[name=100GE0/1/2]",
"value": { "interface": [ { "name": "100GE0/1/2", "type": "iana-if-type:ethernetCsmacd", ... } ] }
```

- **No module prefix.** At the top of a JSON tree a member name carries its module, `ietf-interfaces:interface`. Below a node of the same module it does not ([RFC 7951, section 4](https://www.rfc-editor.org/rfc/rfc7951#section-4)). The router sends `interface`.
- **The rule.** [draft-netana-netmod-yang-anydata-validation-00](https://datatracker.ietf.org/doc/draft-netana-netmod-yang-anydata-validation/) reads the value as a child of the target's parent, the anchor, here `/ietf-interfaces:interfaces` (section 3.3). There the plain name is correct, because `interface` is in the anchor's module (section 4.3.2). Thomas Graf confirmed this reading on the NMOP list on 19 November 2026.
- **The identity.** `iana-if-type:ethernetCsmacd` resolves only when the YANG library lists `iana-if-type` as implemented ([RFC 8525](https://www.rfc-editor.org/rfc/rfc8525)). The router's library 2338653103, published with the IETF 127 capture, does.

| File | Content |
| --- | --- |
| `edit.json` | the target and value of the edit |
| `captured-push-change-update.json` | the whole message, hostnames replaced by `router-a` and `router-b` |
| `library-2338653103.json` | the router's YANG library, cut to the four modules this value needs |
| `yang/` | the IETF modules and the Huawei deviation, at the revisions the library lists |
| `validate.py` | parses the value from the schema root (check A) and from the anchor (check B) |
| `extract-library.py` | cuts the library from the router's reply |

The library comes from `127/Hackathon/yangpush-publisher/huawei-vrp-ip/nc-get-reply/get-yang-library.xml` in the [capture repository](https://github.com/network-analytics/ietf-network-analytics-document-status), 978 modules.
`extract-library.py` keeps the four entries unchanged, with the router's module set, schema and datastore names:

```bash
python3 extract-library.py get-yang-library.xml library-2338653103.json
```

The router does not publish its module files.
The files in `yang/` keep the licence statements in their own files.

## How to reproduce it

With mise on macOS:

```bash
brew install libyang
mise trust && mise install
mise run validate
```

With Docker on any system:

```bash
./run.sh
```

The first Docker build compiles libyang and takes a few minutes.
Both use libyang 6.4.13 and its Python binding 5.1.0.
The parse is strict and parse-only: names, types and identities are checked, mandatory nodes and leafrefs are not, close to the draft's anydata-candidate option.

## Actual result

Read from the schema root, as collectors and consumers do today, libyang rejects the value (check A):

```text
Top-level JSON object member "interface" must be namespace-qualified.
```

The IETF 127 hackathon branches, checked separately on 21 November 2026 outside this repository:

- [mxyns/libyang `feature/anydata-schema-anchor`](https://github.com/mxyns/libyang/tree/feature/anydata-schema-anchor) at 6f395d68, anchored at `/ietf-interfaces:interfaces/interface`, parse-only and strict: the value is valid as sent.
- [NetCalyx `anydata-validation-anchor-poc`](https://github.com/network-analytics/NetCalyx/tree/anydata-validation-anchor-poc) resolves the anchor with `lys_find_xpath` on the target. The router writes the key unquoted, `interface[name=100GE0/1/2]`, which is not valid XPath, and libyang stops with `Invalid character 0x47 ('G')`. With `[name='100GE0/1/2']` it resolves. So this branch rejects the edit before it checks the value.

## Expected result

Read from the anchor, the value validates unchanged (check B).
This is the payload we propose should pass.

`validate.py` prints both checks, each with its prediction:

```text
target: /ietf-interfaces:interfaces/interface[name=100GE0/1/2]
A  library-2338653103.json   anchor /                             fail  (expected fail, ok)
   Top-level JSON object member "interface" must be namespace-qualified. (line 1)
B  library-2338653103.json   anchor /ietf-interfaces:interfaces   pass  (expected pass, ok)
```

Exit code 0 means both checks matched their prediction.
`UNEXPECTED` and exit code 1 mean libyang disagreed.

## Limits

- Only the interface edit is tested. The alarm edit in the same capture has a top-level target and a prefixed value, which matches the draft.
- The draft assumes absolute target paths. RFC 8641 does not state the base of the target.
- The libyang and NetCalyx branches call the target's own schema node the anchor, and the value holds that node. The draft calls the target's parent the anchor. Both read the value the same way.
- The router builds its operational schema from two module sets, `config-module` and `state-module`. libyang 6.4.13 reads only the first module set of a library, so `validate.py` merges the two before loading. Without that step, `iana-if-type` is missing and B fails too.
- The `subscription-started` messages of the IETF 127 capture name content-id 2300156437, an earlier library that lists `iana-if-type` as import-only. With that library B fails on the identity.
