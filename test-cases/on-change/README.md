# Test case: the value of a captured on-change edit

One edit from the IETF 127 capture, validated three ways with libyang 6.4.13.
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
A  library-iana-implemented.json   anchor /                             fail  (expected fail, ok)
   Top-level JSON object member "interface" must be namespace-qualified. (line 1)
B  library-iana-implemented.json   anchor /ietf-interfaces:interfaces   pass  (expected pass, ok)
C  library-as-advertised.json      anchor /ietf-interfaces:interfaces   fail  (expected fail, ok)
   Invalid identityref "iana-if-type:ethernetCsmacd" value - unable to map prefix to YANG schema.: ...
```

## Read the output

Each check parses the same value with a different library or from a different place in the tree.
The columns are: check, YANG library, where the value is read from, libyang's result, the prediction and whether they match.
An indented line is libyang's error.

| Check | `iana-if-type` | Read from | Result | Meaning |
| --- | --- | --- | --- | --- |
| A | implemented | `/` | fail | How collectors and consumers read it today. At the root, `interface` needs the prefix `ietf-interfaces:`. |
| B | implemented | the anchor | **pass** | The value is valid exactly as sent. This is the payload we propose should pass. |
| C | import-only, as advertised | the anchor | fail | The router's own library cannot resolve the identity. |

- **A against B** answers the on-change question: read the value from the parent of the target.
- **B against C** is the periodic-update problem. This payload needs both solved.

Exit code 0 means every check matched its prediction.

## The rule applied

draft-netana-netmod-yang-anydata-validation-00:

- Section 3.3: the value is an instance of the target node. The anchor is the target's parent, here `/ietf-interfaces:interfaces`.
- Section 4.3.2: a first-level name needs a module prefix only at the root, or when its module differs from the anchor's. `interface` is in the anchor's module, so no prefix is correct.

The parse is strict and parse-only: names, types and identities are checked, mandatory nodes and leafrefs are not.
That is close to the draft's anydata-candidate option.

## Files

| File | Content |
| --- | --- |
| `edit.json` | the target and value of the edit |
| `captured-push-change-update.json` | the whole message, hostnames replaced by `router-a` and `router-b` |
| `library-as-advertised.json` | the router's library entries for the modules this value needs |
| `library-iana-implemented.json` | the same, with `iana-if-type` marked implemented |
| `yang/` | the module files the receiver fetched from the router |

The full library has 975 modules, and the files of 436 were never fetched, so only the needed entries are copied.
The YANG modules keep the licence statements in their own files.

## Limits

- Only the interface edit is tested. The alarm edit in the same capture has a top-level target and a prefixed value, which matches the draft.
- The draft assumes absolute target paths. RFC 8641 does not state the base of the target.
- The libyang branch named in section 6 of the draft does not read the value relative to the target, so it is not used.
