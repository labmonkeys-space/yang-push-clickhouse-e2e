# Test case: the value of a captured on-change edit

One edit from the IETF 127 capture, validated two ways with libyang 6.4.13 and the router's YANG library 2338653103.
It shows what the edit value should be validated against, and which payload should pass.

## Run it

With mise on macOS:

```bash
brew install libyang
mise trust && mise install
mise run validate
```

With Docker, `./run.sh`.
The first build compiles libyang and takes a few minutes.

Expected output:

```text
target: /ietf-interfaces:interfaces/interface[name=100GE0/1/2]
A  library-2338653103.json   anchor /                             fail  (expected fail, ok)
   Top-level JSON object member "interface" must be namespace-qualified. (line 1)
B  library-2338653103.json   anchor /ietf-interfaces:interfaces   pass  (expected pass, ok)
```

## Read the output

Both checks parse the same value with the same library, from a different place in the tree.
The columns are: check, YANG library, where the value is read from, libyang's result, the prediction and whether they match.
An indented line is libyang's error.

| Check | Read from | Result | Meaning |
| --- | --- | --- | --- |
| A | `/` | fail | How collectors and consumers read it today. At the root, `interface` needs the prefix `ietf-interfaces:`. |
| B | the anchor | **pass** | The value is valid exactly as sent. This is the payload we propose should pass. |

A against B answers the on-change question: read the value from the parent of the target.

Exit code 0 means every check matched its prediction.

## The rule applied

draft-netana-netmod-yang-anydata-validation-00:

- Section 3.3: the value is an instance of the target node. The anchor is the target's parent, here `/ietf-interfaces:interfaces`.
- Section 4.3.2: a first-level name needs a module prefix only at the root, or when its module differs from the anchor's. `interface` is in the anchor's module, so no prefix is correct.

The implementations use the word differently: in the libyang and NetCalyx branches below, the anchor is the target's own schema node, here `/ietf-interfaces:interfaces/interface`, and the value holds that node.
Both read the value the same way.

The parse is strict and parse-only: names, types and identities are checked, mandatory nodes and leafrefs are not.
That is close to the draft's anydata-candidate option.

## Files

| File | Content |
| --- | --- |
| `edit.json` | the target and value of the edit |
| `captured-push-change-update.json` | the whole message, hostnames replaced by `router-a` and `router-b` |
| `library-2338653103.json` | the router's YANG library, published in the IETF 127 folder |
| `extract-library.py` | copies the entries of the four modules this value needs from a library reply |
| `yang/` | the module files, at the revisions both libraries list |

The library is the router's `127/Hackathon/yangpush-publisher/huawei-vrp-ip/nc-get-reply/get-yang-library.xml` from the [capture repository](https://github.com/network-analytics/ietf-network-analytics-document-status).
It holds 978 modules.
`extract-library.py` keeps the entries of the four modules this value needs, unchanged, with the router's module set, schema and datastore names:

```bash
python3 extract-library.py get-yang-library.xml library-2338653103.json
```

The router does not publish its module files.
The files in `yang/` are the IETF modules and the Huawei deviation at the revisions the library lists.
They keep the licence statements in their own files.

## Limits

- Only the interface edit is tested. The alarm edit in the same capture has a top-level target and a prefixed value, which matches the draft.
- The draft assumes absolute target paths. RFC 8641 does not state the base of the target.
- `validate.py` uses plain libyang 6.4.13 and parses the value under its parent node. It does not use the anchor branches from the IETF 127 hackathon. Checked separately on 21 November 2026, outside this repository:
  - [mxyns/libyang `feature/anydata-schema-anchor`](https://github.com/mxyns/libyang/tree/feature/anydata-schema-anchor) at 6f395d68, `lyd_parse_data_with_anchor` with the anchor `/ietf-interfaces:interfaces/interface`, parse-only and strict: the value is valid as sent, and also with the prefix.
  - [NetCalyx `anydata-validation-anchor-poc`](https://github.com/network-analytics/NetCalyx/tree/anydata-validation-anchor-poc) finds the anchor with `lys_find_xpath` on the edit target. The router writes the key unquoted, `interface[name=100GE0/1/2]`, which is not valid XPath: libyang stops with `Invalid character 0x47 ('G')`. With a quoted key, `[name='100GE0/1/2']`, the target resolves. So that branch rejects this edit before it checks the value.
- The router builds its operational schema from two module sets, `config-module` and `state-module`. libyang 6.4.13 reads only the first module set of a library, so `validate.py` merges the two before loading. Without that step, `iana-if-type` is missing and B fails too.
- The `subscription-started` messages of the IETF 127 capture name content-id 2300156437, an earlier library that lists `iana-if-type` as import-only. With that library B fails on the identity. The test uses the fixed library, 2338653103.
