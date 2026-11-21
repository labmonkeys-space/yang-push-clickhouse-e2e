# Test case: the values of captured on-change edits

## Problem statement

A Huawei NE8000 sends on-change updates as YANG Patch edits inside a `push-change-update` ([RFC 8641](https://www.rfc-editor.org/rfc/rfc8641), [RFC 8072](https://www.rfc-editor.org/rfc/rfc8072)).
Each edit has a `target` and a `value`, and the value holds only the target node, not a tree from the schema root.
Collectors and consumers validate the value from the schema root and reject it.
The question is what the value should be validated against, and which payload should pass.

The IETF 127 capture has two on-change edits with a value: an interface entry (subscription 11) and an alarm (subscription 19).

## Evidence

### Interface edit

```json
"target": "/ietf-interfaces:interfaces/interface[name=100GE0/1/2]",
"value": { "interface": [ { "name": "100GE0/1/2", "type": "iana-if-type:ethernetCsmacd", ... } ] }
```

- **No module prefix.** At the top of a JSON tree a member name carries its module, `ietf-interfaces:interface`. Below a node of the same module it does not ([RFC 7951, section 4](https://www.rfc-editor.org/rfc/rfc7951#section-4)). The router sends `interface`.
- **The rule.** [draft-netana-netmod-yang-anydata-validation-00](https://datatracker.ietf.org/doc/draft-netana-netmod-yang-anydata-validation/) reads the value as a child of the target's parent, the anchor, here `/ietf-interfaces:interfaces` (section 3.3). There the plain name is correct, because `interface` is in the anchor's module (section 4.3.2). Thomas Graf confirmed this reading on the NMOP list on 19 November 2026. The libyang and NetCalyx anchor branches name the target node itself as the anchor and expect the value to hold that node, which reads the value the same way.
- **The target.** A YANG Patch target is a path from the datastore root, "encoded according to the same rules as those for a data resource identifier in a RESTCONF request URI" (RFC 8072, `target-resource-offset`, pointing to [RFC 8040, section 3.5.3](https://www.rfc-editor.org/rfc/rfc8040#section-3.5.3)). RFC 8641, Figure 2, shows the same model: target `/ietf-interfaces:interfaces`, value holding `interfaces`. In that encoding the key is written `interface=100GE0%2F1%2F2`. The router writes `interface[name=100GE0/1/2]`, which is neither that encoding nor valid XPath.
- **The identity.** `iana-if-type:ethernetCsmacd` resolves only when the YANG library lists `iana-if-type` as implemented ([RFC 8525](https://www.rfc-editor.org/rfc/rfc8525)). The router's library 2338653103 does.

### Alarm edit

```json
"target": "/ietf-alarms:alarm-notification",
"value": { "ietf-alarms:alarm-notification": { "alarm-type-id": "LLDP_REMTABLECHANGE", "perceived-severity": "warning", ... } }
```

- **The target is a notification.** `alarm-notification` is a YANG `notification` in ietf-alarms, not a data node. Subscription 19 is a datastore subscription to the operational datastore with this path as its filter, so the edit carries a notification as datastore content.
- **The identity has no module.** `alarm-type-id` is an identityref with base `ietf-alarms:alarm-type-id`. A value without a prefix is looked up in ietf-alarms, which does not define `LLDP_REMTABLECHANGE`. The module that does is not published.

### Library and files

| File | Content |
| --- | --- |
| `edit-interface.json`, `edit-alarm.json` | target and value of each edit |
| `captured-interface-push-change-update.json`, `captured-alarm-push-change-update.json` | the whole messages, hostnames replaced by `router-a` and `router-b` |
| `library-2338653103.json` | the router's YANG library, cut to the modules the two values need |
| `yang/` | the IETF modules and the Huawei interface deviation, at the revisions the library lists |
| `validate.py` | runs the checks below |
| `extract-library.py` | cuts the library from the router's reply |

The library is `127/Hackathon/yangpush-publisher/huawei-vrp-ip/nc-get-reply/get-yang-library.xml` from the [capture repository](https://github.com/network-analytics/ietf-network-analytics-document-status), 978 modules.
`extract-library.py` keeps the needed entries unchanged, with the router's module set, schema and datastore names:

```bash
python3 extract-library.py get-yang-library.xml library-2338653103.json
```

The `subscription-started` messages of the capture name content-id 2300156437, an earlier library from the IETF 126 folder that lists `iana-if-type` as import-only.
With that library check B fails on the identity.
2338653103 is the router's fix.

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

How `validate.py` checks:

- **Strict and parse-only.** Names, types and identities are checked. Mandatory nodes and leafrefs are not. That is close to the draft's anydata-candidate option.
- **One module set.** The router builds its operational schema from `config-module` and `state-module`. libyang reads only the first module set of a library, as `ly_ctx_new_ylpath` in `context.h` documents, so `validate.py` merges the two before loading.
- **Unpublished deviations left out.** The library lists `huawei-ietf-alarms-deviations-NE8000-F1A` for ietf-alarms. The router does not publish it, so `validate.py` leaves it out and prints its name. A deviation changes nodes of ietf-alarms. It cannot turn the notification into data or define the missing identity.

## Actual result

- **Interface edit, from the schema root** (check A), as collectors and consumers read it today: `Top-level JSON object member "interface" must be namespace-qualified.`
- **Alarm edit, from the root** (check C), which is the anchor of a top-level target: `Unexpected notification element "alarm-notification".`
- **Alarm edit, read as a notification** (check D): `Invalid identityref "LLDP_REMTABLECHANGE" value - identity not found in module "ietf-alarms".`

The IETF 127 hackathon branches, checked separately on 21 November 2026 outside this repository, on the interface edit:

- [mxyns/libyang `feature/anydata-schema-anchor`](https://github.com/mxyns/libyang/tree/feature/anydata-schema-anchor) at 6f395d68, anchored at `/ietf-interfaces:interfaces/interface`, parse-only and strict: the value is valid as sent.
- [NetCalyx `anydata-validation-anchor-poc`](https://github.com/network-analytics/NetCalyx/tree/anydata-validation-anchor-poc) resolves the anchor with `lys_find_xpath` on the target. libyang stops at the unquoted key with `Invalid character 0x47 ('G')`. With `[name='100GE0/1/2']` it resolves.

## Expected result

- **Interface edit, from the anchor** (check B): the value validates unchanged. This is the payload we propose should pass.
- **Alarm edit:** no reading can pass. The router would have to send the alarm as a notification, not as a datastore edit, and qualify `alarm-type-id` with the module that defines it.

`validate.py` prints each edit, its checks with their prediction, and the result:

```text
Library: library-2338653103.json (iana-if-type implemented)
Not published, left out: huawei-ietf-alarms-deviations-NE8000-F1A

Interface edit, subscription 11
   target: /ietf-interfaces:interfaces/interface[name=100GE0/1/2]
   value:  {"interface": ...}  (no module prefix)
A  read from the schema root, as collectors do today               fail  (predicted fail, ok)
   Top-level JSON object member "interface" must be namespace-qualified. (line 1)
B  read from the target's parent, the draft's anchor               pass  (predicted pass, ok)

Alarm edit, subscription 19
   target: /ietf-alarms:alarm-notification
   value:  {"ietf-alarms:alarm-notification": ...}  (with module prefix)
C  read from the schema root, the anchor of a top-level target     fail  (predicted fail, ok)
   Unexpected notification element "alarm-notification".: Schema path: /ietf-alarms:alarm-notification (line 1)
D  read as a YANG notification instead                             fail  (predicted fail, ok)
   Invalid identityref "LLDP_REMTABLECHANGE" value - identity not found in module "ietf-alarms".: Data path: /ietf-alarms:alarm-notification/alarm-type-i

Result: the interface value is valid as sent. It fails only when read from the schema root.
        The alarm value cannot pass: its target is a notification, not data,
        and its alarm-type-id names an identity the library does not define.
```

Exit code 0 means every check matched its prediction.
`UNEXPECTED` and exit code 1 mean libyang disagreed.

## Limits

- **Parse-only.** Leafrefs and mandatory nodes are not checked. Full validation of the interface value fails on `higher-layer-if`, which points to `100GE0/1/2.23`, an interface that is not part of this one-entry value.
- **Unpublished modules.** The router does not publish its module files. `yang/` holds the IETF modules at the listed revisions and the Huawei interface deviation. The alarm deviation and the module that defines `LLDP_REMTABLECHANGE` are missing.
- **Third edit not tested.** Subscription 17 sends an LLDP `delete` edit, which has no value to validate.
