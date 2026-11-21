# Test case: the values of captured on-change edits

**Answer:** the interface value validates against the schema node `/ietf-interfaces:interfaces`, the target's parent, with the fixed library 2338653103 and the modules in `yang/`, strict and parse-only (check B2).
It fails with the advertised library 2300156437 (B1) and under full validation (H2).
The value passes, but the target as sent does not resolve (E1, E2). These are separate results.

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
- **The identity.** `iana-if-type:ethernetCsmacd` resolves only when the YANG library lists `iana-if-type` as implemented ([RFC 8525](https://www.rfc-editor.org/rfc/rfc8525)). The fixed library 2338653103 does. The advertised library 2300156437 lists it as import-only.

### Alarm edit

```json
"target": "/ietf-alarms:alarm-notification",
"value": { "ietf-alarms:alarm-notification": { "alarm-type-id": "LLDP_REMTABLECHANGE", "perceived-severity": "warning", ... } }
```

- **The target is a notification.** `alarm-notification` is a YANG `notification` in ietf-alarms, not a data node. Subscription 19 is a datastore subscription to the operational datastore with this path as its filter, so the edit carries a notification as datastore content.
- **The identity has no module.** `alarm-type-id` is an identityref with base `ietf-alarms:alarm-type-id`. A value without a prefix is looked up in ietf-alarms, which does not define `LLDP_REMTABLECHANGE`. The library also lists `huawei-ietf-alarms-deviations-NE8000-F1A` (revision 2025-02-24) for ietf-alarms. A deviation could change the type of `alarm-type-id`, but that module is not published (see Limits).

### Library and files

| File | Content |
| --- | --- |
| `edit-interface.json`, `edit-alarm.json` | target and value of each edit |
| `captured-interface-push-change-update.json`, `captured-alarm-push-change-update.json` | the whole messages, hostnames replaced by `router-a` and `router-b` |
| `library-2300156437.json` | the advertised library, cut to the modules the two values need |
| `library-2338653103.json` | the fixed library, cut the same way |
| `expected-results.json` | every check with its inputs and its prediction |
| `yang/` | the modules the two values need, at the revisions the library lists |
| `validate.py` | runs the checks below |
| `extract-library.py` | cuts the library from the router's reply |

Both libraries come from the [capture repository](https://github.com/network-analytics/ietf-network-analytics-document-status), from `<folder>/Hackathon/yangpush-publisher/huawei-vrp-ip/nc-get-reply/get-yang-library.xml`:

- **2300156437, the advertised library**, from the IETF 126 folder. Every `subscription-started` of the IETF 127 capture names this content-id. It lists `iana-if-type` as import-only.
- **2338653103, the fixed library**, from the IETF 127 folder, 978 modules. It lists `iana-if-type` as implemented.

`extract-library.py` keeps the needed entries unchanged, with the router's module set, schema and datastore names.
It fails when `yang/` has no file at a revision the library names:

```bash
python3 extract-library.py 126/get-yang-library.xml library-2300156437.json
python3 extract-library.py 127/get-yang-library.xml library-2338653103.json
```

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

- **Two libraries.** Every check runs once per library. Checks ending in 1 use the advertised library 2300156437, checks ending in 2 the fixed library 2338653103.
- **Strict and parse-only.** Names, types and identities are checked. Mandatory nodes and leafrefs are not. That is close to the draft's anydata-candidate option. Check H2 is the one exception: full validation, which also checks leafrefs and mandatory nodes.
- **Predictions in a file.** `expected-results.json` holds each check's inputs and prediction. A predicted fail also names part of libyang's error, so a check that fails for another reason does not match.
- **One module set.** The router builds its operational schema from `config-module` and `state-module`. libyang reads only the first module set of a library, as `ly_ctx_new_ylpath` in `context.h` documents, so `validate.py` merges the two before loading.
- **Missing deviation left out.** The library lists `huawei-ietf-alarms-deviations-NE8000-F1A` for ietf-alarms, and no copy of it was found (see Limits). `validate.py` leaves it out and prints its name. A deviation cannot turn the notification into data, so checks C1 and C2 do not depend on it. It could change the type of `alarm-type-id`, so checks D1 and D2 do.

## Actual result

- **Interface edit, from the schema root** (A1, A2), as collectors and consumers read it today: `Top-level JSON object member "interface" must be namespace-qualified.`
- **Interface edit, from the anchor, advertised library** (B1): `Invalid identityref "iana-if-type:ethernetCsmacd" value - unable to map prefix to YANG schema.` The NetGauze collector logged the same error for the periodic interface updates of the IETF 127 run.
- **Interface edit, from the anchor, full validation** (H2): `Invalid leafref value "100GE0/1/2.23" - no target instance`. The value holds one interface, and `higher-layer-if` points to another.
- **Alarm edit, from the root** (C1, C2), which is the anchor of a top-level target: `Unexpected notification element "alarm-notification".`
- **Alarm edit, read as a notification** (D1, D2): `Invalid identityref "LLDP_REMTABLECHANGE" value - identity not found in module "ietf-alarms".`
- **Interface target, resolved as XPath** (E to G, the same with both libraries). The [NetCalyx `anydata-validation-anchor-poc`](https://github.com/network-analytics/NetCalyx/tree/anydata-validation-anchor-poc) branch finds the anchor with `lys_find_xpath` on the target, and `validate.py` does the same with plain libyang. The router's form fails with `Invalid character 0x47 ('G')` (E). The RFC 8040 encoding fails the same way (G). Only a quoted key resolves (F).

The [mxyns/libyang `feature/anydata-schema-anchor`](https://github.com/mxyns/libyang/tree/feature/anydata-schema-anchor) branch, checked separately on 21 November 2026 at 6f395d68, anchored at `/ietf-interfaces:interfaces/interface`, parse-only and strict: the interface value is valid as sent.

## Expected result

- **Interface edit, from the anchor, fixed library, parse-only** (B2): the value validates unchanged. This is the payload we propose should pass.
- **The same with the advertised library** (B1): it cannot pass, because `iana-if-type` is import-only there.
- **Alarm edit:** no reading can pass. The router would have to send the alarm as a notification, not as a datastore edit, and qualify `alarm-type-id` with the module that defines it.
- **Interface target:** a collector needs one agreed spelling. RFC 8072 asks for the RFC 8040 encoding, `interface=100GE0%2F1%2F2`, which an XPath lookup cannot read (G). The router sends neither that nor valid XPath (E).

`validate.py` prints each edit and the target lookup, the checks with their prediction, and the result:

```text
Library: library-2300156437.json, the advertised library, named by every subscription-started; iana-if-type import-only
Not found, left out: huawei-ietf-alarms-deviations-NE8000-F1A

Interface edit, subscription 11
   target: /ietf-interfaces:interfaces/interface[name=100GE0/1/2]
   value:  {"interface": ...}  (no module prefix)
A1  read from the schema root, as collectors do today               fail  (predicted fail, ok)
   Top-level JSON object member "interface" must be namespace-qualified. (line 1)
B1  read from the target's parent, the draft's anchor               fail  (predicted fail, ok)
   Invalid identityref "iana-if-type:ethernetCsmacd" value - unable to map prefix to YANG schema.: Data path: /ietf-interfaces:interfaces/interface[name=

Alarm edit, subscription 19
   target: /ietf-alarms:alarm-notification
   value:  {"ietf-alarms:alarm-notification": ...}  (with module prefix)
C1  read from the schema root, the anchor of a top-level target     fail  (predicted fail, ok)
   Unexpected notification element "alarm-notification".: Schema path: /ietf-alarms:alarm-notification (line 1)
D1  read as a YANG notification instead                             fail  (predicted fail, ok)
   Invalid identityref "LLDP_REMTABLECHANGE" value - identity not found in module "ietf-alarms".: Data path: /ietf-alarms:alarm-notification/alarm-type-i

Interface target, resolved as XPath like the NetCalyx anchor branch
E1  as the router sends it                                          fail  (predicted fail, ok)
   /ietf-interfaces:interfaces/interface[name=100GE0/1/2]
   Invalid character 0x47 ('G'), perhaps "100" is supposed to be a function call.
F1  with a quoted key                                               pass  (predicted pass, ok)
   /ietf-interfaces:interfaces/interface[name='100GE0/1/2']
   schema node /ietf-interfaces:interfaces/interface
G1  in the RFC 8040 encoding of RFC 8072                            fail  (predicted fail, ok)
   /ietf-interfaces:interfaces/interface=100GE0%2F1%2F2
   Invalid character 0x47 ('G'), perhaps "100" is supposed to be a function call.

Library: library-2338653103.json, the fixed library, published with the IETF 127 capture; iana-if-type implemented
Not found, left out: huawei-ietf-alarms-deviations-NE8000-F1A

Interface edit, subscription 11
   target: /ietf-interfaces:interfaces/interface[name=100GE0/1/2]
   value:  {"interface": ...}  (no module prefix)
A2  read from the schema root, as collectors do today               fail  (predicted fail, ok)
   Top-level JSON object member "interface" must be namespace-qualified. (line 1)
B2  read from the target's parent, the draft's anchor               pass  (predicted pass, ok)
H2  read from the target's parent, full validation                  fail  (predicted fail, ok)
   Invalid leafref value "100GE0/1/2.23" - no target instance "/if:interfaces/if:interface/if:name" with the same value.: Data path: /ietf-interfaces:int

Alarm edit, subscription 19
   target: /ietf-alarms:alarm-notification
   value:  {"ietf-alarms:alarm-notification": ...}  (with module prefix)
C2  read from the schema root, the anchor of a top-level target     fail  (predicted fail, ok)
   Unexpected notification element "alarm-notification".: Schema path: /ietf-alarms:alarm-notification (line 1)
D2  read as a YANG notification instead                             fail  (predicted fail, ok)
   Invalid identityref "LLDP_REMTABLECHANGE" value - identity not found in module "ietf-alarms".: Data path: /ietf-alarms:alarm-notification/alarm-type-i

Interface target, resolved as XPath like the NetCalyx anchor branch
E2  as the router sends it                                          fail  (predicted fail, ok)
   /ietf-interfaces:interfaces/interface[name=100GE0/1/2]
   Invalid character 0x47 ('G'), perhaps "100" is supposed to be a function call.
F2  with a quoted key                                               pass  (predicted pass, ok)
   /ietf-interfaces:interfaces/interface[name='100GE0/1/2']
   schema node /ietf-interfaces:interfaces/interface
G2  in the RFC 8040 encoding of RFC 8072                            fail  (predicted fail, ok)
   /ietf-interfaces:interfaces/interface=100GE0%2F1%2F2
   Invalid character 0x47 ('G'), perhaps "100" is supposed to be a function call.

Result: the interface value is valid as sent with the fixed library, read from
        the target's parent. It fails when read from the schema root, and with
        the advertised library, which lists iana-if-type as import-only.
        Full validation fails on higher-layer-if, which points to an
        interface outside this one-entry value.
        The alarm value cannot pass: its target is a notification, not data,
        and its alarm-type-id names an identity ietf-alarms does not define,
        unless the missing deviation changes the type of alarm-type-id.
        An XPath lookup of the target works only with a quoted key, which
        neither the router's form nor the RFC 8040 encoding has.
```

Exit code 0 means every check matched its prediction.
`UNEXPECTED` and exit code 1 mean libyang disagreed.

## Limits

- **Parse-only.** Apart from H2, leafrefs and mandatory nodes are not checked. H2 shows that full validation of a one-entry value fails on `higher-layer-if`.
- **Where the modules come from.** `ietf-interfaces`, `ietf-yang-types` and `huawei-ietf-interfaces-deviations-NE8000-F1A@2026-02-24` are byte-identical to the files the router served to NetCalyx, in the receiver cache `127/Hackathon/mb-alive-test-result/netcalyx/netcalyx-yangpush-receiver/alive-netcalyx-receiver-cache-content.tar.gz` in the capture repository. Huawei's own [YANG repository](https://github.com/Huawei/yang) has the interface deviation only at revision 2021-05-07. No receiver cache has `iana-if-type` or `ietf-alarms`, because neither is named in `module-version` or imported: `iana-if-type@2017-01-19` is the IANA module at the revision the library lists, as nl6 ships it, and `ietf-alarms@2019-09-11` comes from [YangModels](https://github.com/YangModels/yang). Both are named by the library, but their text is not captured. `expected-results.json` lists them under `not-captured`.
- **Missing alarm modules.** `huawei-ietf-alarms-deviations-NE8000-F1A` is not in the capture repository, its receiver and consumer caches of IETF 126 and 127, or Huawei's YANG repository (checked 21 November 2026). The module that defines `LLDP_REMTABLECHANGE` is unknown. Checks D1 and D2 can only be settled with them.
- **Third edit not tested.** Subscription 17 sends an LLDP `delete` edit, which has no value to validate.
