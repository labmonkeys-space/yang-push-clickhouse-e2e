# Test case: collector and consumer on captured periodic updates

**Answer:** with the router's advertised library, the collector and the consumer agree: both drop the interface update of subscription 6, with the same error (P1, P3).
With the fixed library the collector passes and the consumer still fails (P2, P3).
That is where the chain diverges.
The control, subscription 3, passes on both sides (P4, P5).

## Problem statement

Dan Voyer asked on the NMOP list on 20 November 2026:

> For the periodic update, I'd like to see if the collector and a second consumer agree, using the device's advertised YANG library and the schema referenced by the Schema ID, without adding anything locally.

A Huawei NE8000 sends periodic `push-update` messages for `/ietf-interfaces:interfaces/interface` ([RFC 8641](https://www.rfc-editor.org/rfc/rfc8641)).
Its `subscription-started` lists only `ietf-interfaces` in `module-version`.
The entries carry identity values such as `iana-if-type:ethernetCsmacd`.
`ietf-interfaces` does not import `iana-if-type`, so the dependency rule of [draft-ietf-nmop-yang-message-broker-integration](https://datatracker.ietf.org/doc/draft-ietf-nmop-yang-message-broker-integration/) (section 4.3) does not reach it.
The question is whether the collector, which has the device's library, and the consumer, which has only the registered schema, reach the same verdict.

## Evidence

All capture files are in the [capture repository](https://github.com/network-analytics/ietf-network-analytics-document-status), folder `127/Hackathon/mb-alive-test-result/`.

- **The collector dropped every interface update.** The NetGauze receiver log (`netgauze/netgauze-yangpush-receiver/alive-netgauze-receiver-log.tar.gz`) drops all push-updates of subscriptions 6 and 12 with `Invalid identityref "iana-if-type:ethernetCsmacd" value - unable to map prefix to YANG schema.` `netgauze/readme.md` reports the same.
- **So no Schema ID exists for them.** The registry readmes of both runs list "6, 11, 12: no message".
- **Schema 628 stands in.** For subscription 6 the collector used content-id `453f4d25…`. The same content-id registered as schema 628 in the NetGauze run, and subscriptions 2 to 5 carry it in their Kafka headers. Its references reach 582 modules: `ietf-interfaces` and `huawei-devm` are among them, `iana-if-type` is not.
- **The NetCalyx registry dump is empty.** Its three files hold `{}`, so this case uses the NetGauze dump.

### Libraries and files

Both libraries come from `<folder>/Hackathon/yangpush-publisher/huawei-vrp-ip/nc-get-reply/get-yang-library.xml`:

- **2300156437, the advertised library**, from the IETF 126 folder. Every `subscription-started` of the IETF 127 capture names this content-id. It lists `iana-if-type` as import-only.
- **2338653103, the fixed library**, from the IETF 127 folder. It lists `iana-if-type` as implemented.

| File | Content |
| --- | --- |
| `push-update-s06.json`, `push-update-s03.json` | the captured push-updates, hostnames replaced by `router-a`, `router-b` and so on |
| `library-2300156437.json`, `library-2338653103.json` | the two libraries, cut to the modules the collector finds for the updates |
| `collector/` | the texts of those modules, at the revisions the libraries name |
| `consumer/` | schema 628 and the 582 modules its references reach, with `features.json` from the registry metadata |
| `expected-results.json` | every check with its inputs and its prediction |
| `SOURCES.md` | the capture file of every input, with its SHA-256 |
| `prepare.py` | cuts all of the above from the capture files |
| `validate.py` | runs the checks |

`prepare.py` builds both sides:

- **Collector.** The modules a receiver finds through the library for one update: the modules of `module-version`, the modules whose names prefix a member or an identity value in the payload and that the library lists, their imports, includes and the deviations the library lists. The texts come from the NetCalyx receiver cache of content-id `453f4d25…`. It fails when the cache has no text at the revision the library names.
- **Consumer.** Schema 628 and every schema its references reach. References name a subject and a version, and the registrations in the NetGauze collector log map them to schema IDs. It fails when `iana-if-type` reaches the closure, because then P3 would not test the gap.

To cut the inputs again, check out the capture repository and run:

```bash
python3 -I prepare.py <capture-repository> .
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

- **From the schema root.** The `datastore-contents` of each update is parsed as top-level data, strict and parse-only. Names, types and identities are checked. Mandatory nodes and leafrefs are not.
- **Collector schema.** libyang loads the library cut with its module sets merged, because libyang reads only the first module set (`ly_ctx_new_ylpath` in `context.h`).
- **Consumer schema.** Every module of the closure is loaded as implemented, with the features the registry lists, and compiled once.
- **Predictions in a file.** A predicted fail also names part of libyang's error, so a check that fails for another reason does not match.
- **The state of `iana-if-type`.** For each check the output shows how the library or the registry closure lists it, and how the libyang context holds it.

## Actual result

- **P1, collector, advertised library:** `Invalid identityref "iana-if-type:ppp" value - unable to map prefix to YANG schema.` The library lists `iana-if-type` as import-only. libyang loads no import-only module that nothing imports, so the module is not in the context at all.
- **P2, collector, fixed library:** passes.
- **P3, consumer, schema 628:** the same error as P1. The closure does not reach `iana-if-type`.
- **P4 and P5, subscription 3:** pass on both sides.

## Expected result

- **With the advertised library** (P1, P3): collector and consumer agree on fail. Passing at the consumer after adding `iana-if-type` locally would not show that the chain agrees, because the collector still drops the update.
- **With the fixed library** (P2, P3): the collector passes and the consumer fails. The collector can find `iana-if-type` through the library, but the Schema ID gives the consumer no way to name it. That is the "YANG identityref schema dependency resolution" open item of the IETF 127 NMOP slides.
- **Subscription 3** (P4, P5): both pass. The two sides agree when the update needs no module outside the dependency rule.

`validate.py` prints each update, the checks with their prediction, and the result:

```text
Subscription 6, /ietf-interfaces:interfaces/interface, module-version ietf-interfaces
P1  collector  advertised library 2300156437     fail  (predicted fail, ok)
    iana-if-type: import-only in the library, absent in libyang
    Invalid identityref "iana-if-type:ppp" value - unable to map prefix to YANG schema.: Data path: /ietf-interfaces:interfaces/interface[name='Virtual-Te
P2  collector  fixed library 2338653103          pass  (predicted pass, ok)
    iana-if-type: implemented in the library, implemented in libyang
P3  consumer   schema 628 and its references     fail  (predicted fail, ok)
    iana-if-type: not in the registry closure, absent in libyang
    Invalid identityref "iana-if-type:ppp" value - unable to map prefix to YANG schema.: Data path: /ietf-interfaces:interfaces/interface[name='Virtual-Te

Subscription 3, /huawei-devm:devm/physical-entitys/physical-entity, module-version huawei-devm
P4  collector  advertised library 2300156437     pass  (predicted pass, ok)
    iana-if-type: import-only in the library, absent in libyang
P5  consumer   schema 628 and its references     pass  (predicted pass, ok)
    iana-if-type: not in the registry closure, absent in libyang

Result: subscription 6, the interfaces.
        With the advertised library, collector and consumer agree on fail,
        with the same error. The library lists iana-if-type as import-only,
        and libyang loads no import-only module that nothing imports, so the
        collector's schema lacks it just as schema 628 does.
        With the fixed library the collector passes and the consumer still fails.
        Subscription 3, the control: collector and consumer agree on pass.
```

Exit code 0 means every check matched its prediction.
`UNEXPECTED` and exit code 1 mean libyang disagreed.

## Running with another implementation

`expected-results.json` lists each check with its payload, its role, its schema (a library file, or registry schema 628) and its prediction.
Load the same files into your collector or consumer, run each check, and report pass or fail per check ID.
A result that differs from the prediction is the divergence this case looks for.

## Limits

- **No Schema ID existed for subscription 6.** The collector dropped every update, so none reached the producer. Schema 628 is the schema of the content-id the collector used for it.
- **One module text is not captured.** No receiver fetched `iana-if-type`, because no `module-version` names it. `collector/iana-if-type@2017-01-19.yang` is the IANA module at the revision both libraries name, as nl6 ships it. It is on the collector side only, where the library names it.
- **Revisions limit the fixed library to subscription 6.** Library 2338653103 names `huawei-devm@2026-07-23`, and the captures hold only revision 2026-06-11. Subscription 3 is checked with the advertised library only.
- **The collector is modelled, not run.** Today's collectors build their schema from `module-version` and its dependencies, so they do not find `iana-if-type` even with the fixed library. P2 shows what a collector that looks up payload prefixes in the library would get. Running NetCalyx and NetGauze against these inputs is left for IETF 128.
- **Parse-only.** Leafrefs and mandatory nodes are not checked.
