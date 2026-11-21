# yang-push-clickhouse-e2e

A proof of concept for an end-to-end test of the IETF NMOP YANG-Push message broker chain into ClickHouse.
Simulated HUAWEI NE 8000 routers from [nl6](https://github.com/labmonkeys-space/nl6) are the YANG-Push publishers.
They replace the PCAP replay used in the [IETF 127 hackathon](https://github.com/network-analytics/ietf-network-analytics-document-status/tree/main/127/Hackathon).

```
nl6 (HUAWEI NE 8000)  --UDP-Notif-->  NetCalyx receiver  -->  NetCalyx producer
        ^                                   |                       |
        +------ NETCONF: YANG library ------+                       v
                                              Kafka + YANG schema registry
                                                                    |
                                                                    v
                                              consumer (libyang)  -->  ClickHouse  <--  ClickStack UI
```

## What differs from the hackathon setup

| Hackathon | Here |
| --- | --- |
| PCAP replay with traffic-reproducer | Live nl6 devices, 3 by default |
| Pre-seeded NetCalyx YANG cache, dummy NETCONF block | NetCalyx fetches the YANG library and the subscriptions from the devices over NETCONF |
| Fixed capture | Counters move, and `make link-down` triggers an on-change update |
| ClickHouse's own Python consumer | A consumer written from the description in the slides |

The workload is narrow on purpose.
Each device runs three `ietf-interfaces` subscriptions: 8 (statistics, periodic 60 s), 9 (interfaces, periodic 60 s) and 10 (admin and oper status, on-change).

## Run it

Needs a Linux Docker engine with Compose, `make`, `curl` and `ssh-keygen`.
nl6 needs `/dev/net/tun`, `NET_ADMIN` and `SYS_ADMIN` inside its container.
NetCalyx ships amd64 only, so on arm64 it runs under emulation.

```bash
make e2e      # clean stack, 3 devices, wait 150 s, on-change event, verify
make verify   # print the result table again
make sql Q="SELECT name, in_octets FROM telemetry_schema_63_sub_8 ORDER BY kafka_timestamp DESC LIMIT 5"
make down
```

`make help` lists every target.
`make devices DEVICES=20` adds a bigger fleet.
`NL6_IMAGE=<image> make e2e` runs the chain against another nl6 build.

## Use ClickStack

ClickStack is the ClickHouse observability UI, also known as HyperDX.
It starts with the stack and reads the same ClickHouse the consumer writes.

### Log in

1. Run `make up devices`, or `make e2e` for a full run.
2. Open http://localhost:8090.
3. Log in with `demo@example.com` and `YangPush-e2e-1`. `make ui` prints both.

The login exists because the ClickStack app image requires one.
The port is bound to localhost only.
A setup container creates the user, the `ClickHouse` connection, the `YANG messages` source and the dashboard at start.

Data exists only since the stack started.
Set the time range to the last 15 minutes or the last hour.
The first periodic push-updates arrive up to 60 seconds after `make devices`.

### Open the dashboard

Open **Dashboards** and pick **YANG-Push overview**.

| Tile | What it shows |
| --- | --- |
| Messages consumed, valid, invalid | Kafka records in ClickHouse and their validation result |
| Routers publishing | Distinct export addresses |
| Traffic in and out per router (bit/s) | Rates from the `in-octets` and `out-octets` counters of subscription 8, summed over the interfaces of a router |
| Messages per subscription | Records per interval, split by subscription and validity |
| Invalid messages by cause | Failing stage and libyang error, see the findings below |
| Interfaces not up, latest state | From subscription 10. Empty as long as every interface is up |
| Busiest interfaces, latest octet counters | Top 20 interfaces by `in-octets` |

The rate tiles need two readings per interface, so they fill in after the second push-update.
Turn on live mode, or refresh, to follow a running stack.

### Search the messages

Open **Search** and select the source **YANG messages**.
Each row is one Kafka record with its router, subscription, notification type and validation result.
The notification JSON is the searched text.

Useful filters, with the query language set to SQL:

```sql
NOT valid
subscription_id = 8
hostname = '<router hostname>'
notification_type = 'ietf-yang-push:push-change-update'
notification ILIKE '%100GE1/0/3%'
```

`make link-down` takes an interface of the first router down and up.
The two on-change updates then match the `push-change-update` filter.

### Add your own tile

The dashboard tiles are plain ClickHouse SQL.
In the dashboard, add a tile, choose the SQL editor and the `ClickHouse` connection.
ClickStack fills in three query parameters: the time range and the chart interval.

A time series needs a time column first, then the series name, then the value:

```sql
SELECT
  toStartOfInterval(kafka_timestamp, INTERVAL {intervalSeconds:Int64} SECOND) AS ts,
  hostname,
  sum(in_errors) AS in_errors
FROM yang.yang_interfaces_interface_statistics
WHERE kafka_timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64})
  AND kafka_timestamp <  fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY ts, hostname
ORDER BY ts ASC
```

Query the views with stable names, not the `telemetry_schema_*` tables.
The table names hold the schema ID and the subscription ID, which change between runs.
The consumer keeps the views pointed at the current tables:

| View | Content |
| --- | --- |
| `yang.yang_messages` | One row per Kafka record |
| `yang.yang_interfaces_interface_statistics` | Interface counters per router, interface and reading |
| `yang.yang_interfaces_interface_oper_status__interfaces_interface_admin_status` | Admin and oper status per router and interface |

A view appears with the first valid push-update of its subscription.
`make sql Q="SHOW TABLES"` lists what exists.

### Keep a dashboard change

Changes made in the UI live in the MongoDB container and are gone after `make down`.
To keep a change, put the tile into `clickstack/dashboard.json` and load it:

```bash
docker compose run --rm clickstack-setup
```

The setup replaces the dashboard named `YANG-Push overview` with the file.
Write `__CONNECTION__` as the tile's connection. The setup swaps in the real connection ID.

## How the consumer loads ClickHouse

1. **Consume.** Batches of up to 500 Kafka records. The schema ID comes from the `schema-id` header.
2. **Resolve schema.** The YANG modules are fetched from the registry once per schema ID, following references, and cached on disk.
3. **Validate.** One strict libyang parse covers the message, the envelope, the notification and the pushed data. The failing stage is read from the path libyang reports. The NetCalyx reference consumer validates the same way.
4. **Map.** The subscription's xpath target is walked in the libyang schema. List keys and every leaf below the target become typed columns.
5. **Insert.** One `JSONEachRow` insert per table and batch. Kafka offsets are committed afterwards.

Tables are created on first use.

- `telemetry_schema_<schema>_messages` has one row per Kafka record, valid or not, with the validation result.
- `telemetry_schema_<schema>_sub_<subscription>` has one row per object in each valid push-update.
- One view per xpath target, named `yang_<path>`, joins the tables of every schema and subscription with that target.

## Result

`make e2e` on 18 November 2026, OrbStack Linux engine on arm64 (kernel 7.2, Docker 29.8.2), three devices, about three minutes of traffic:

```
Messages sent by nl6:         32
Messages decoded by NetCalyx: 32
Messages produced to Kafka:   32
Messages in ClickHouse:       32
Messages valid:               21
Invalid, known finding:       11
Invalid, unexpected:          0

Schema  Sub Devices Messages  Valid     Rows  Xpath
    63    8       3       12     12      864  /ietf-interfaces:interfaces/interface/statistics
    63    9       3       12      3        0  /ietf-interfaces:interfaces/interface
    63   10       3        8      6      288  /ietf-interfaces:interfaces/interface/oper-status | /ietf-interfaces:interfaces/interface/admin-status

PASSED: every message nl6 sent is in ClickHouse
```

Every message nl6 sent arrived in ClickHouse.
The consumer obtained the schema of the device library (schema ID 63, 35 modules) from the registry.
Subscription 8 is fully valid.
Subscription 9 push-updates and the two on-change updates of subscription 10 fail the data stage, see finding 2.
NetCalyx itself accepts all 32, because its strict anydata validation is off.

## What `make verify` checks

It follows the message count through the chain and fails when the four counts differ:

| Count | Source |
| --- | --- |
| Sent by nl6 | `messages_sent` from `GET /api/v1/yangpush/status` |
| Decoded by NetCalyx | its `netcalyx_udp_notif_decoder_decoded` counter |
| Produced to Kafka | the topic's offsets |
| In ClickHouse | rows in `yang_messages` |

The first count that drops shows where messages are lost.
The devices keep sending, so the check retries for up to 40 seconds until the counts agree.

These are totals for the whole stack since it started.
There is no loss ratio per device and no tolerance band.
That needs YANG-Push as an nl6 load-test scenario protocol, so `nl6-reconcile` can be used: [nl6#1061](https://github.com/labmonkeys-space/nl6/issues/1061).

## Findings

1. **NetCalyx turns the registry's compatibility check off per subject.**
   NetCalyx registers its bundled default library first.
   With the registry default `BACKWARD`, most modules of the device library are then rejected with 409.
   NetCalyx v0.4.0 logs a warning, sets the subject to `NONE` and registers again.
   The stack sets `NONE` globally, which only avoids those warnings.
2. **Two message shapes fail strict validation at the consumer.**
   Both are the consumer-side view of [nl6#1017](https://github.com/labmonkeys-space/nl6/issues/1017), and the captured NE 8000 sends both shapes.
   Subscription 9 push-updates: the registered schema lacks `iana-if-type`, so libyang cannot resolve `iana-if-type:ethernetCsmacd`.
   NetCalyx builds a reduced library from the modules named in `module-version`, their imports and `augmented-by` entries. `iana-if-type` is none of those, so NetCalyx never fetches it from the device.
   Subscription 10 on-change updates: the YANG Patch value is rooted at the `interface` list, which is not a top-level node.
   [test-cases/on-change](test-cases/on-change) checks the on-change values of the IETF 127 capture with libyang: the interface value passes when read from the parent of the edit target.
   [test-cases/periodic](test-cases/periodic) checks the periodic interface updates of the IETF 127 capture as a collector and as a consumer: both fail with the advertised library. With the fixed one, a modelled collector that looks up payload prefixes in the library passes, while the registered schema, built by the module-version rule, still lacks `iana-if-type`.
   `make verify` reports these messages as a known finding and does not fail on them.
3. **The NetCalyx `.deb` ships no YANG assets.**
   The YANG producer needs `assets/yang/ietf-telemetry-message`, so the image adds it from the NetCalyx repository at the release commit.
4. **The NetCalyx `.deb` needs `ca-certificates`.**
   Without it the collector panics when it creates the schema registry client, even for a plain http registry.

5. **The Confluent 8.x registry image has no `curl`.**
   The health check opens the port with bash instead.
6. **NetCalyx drops what arrives while it starts.**
   Its UDP-Notif socket decodes messages before the rest of its pipeline is up, and those messages never reach Kafka.
   One run lost the first 12 of 38 messages this way: nine `subscription-started` and three sync push-updates.
   The four-count check found it. `make up` now waits until NetCalyx has registered its default schema before devices may be created.

## Limits

- The consumer is not ClickHouse's consumer. Its table layout follows the slides, not their code.
- Validation skips mandatory-node checks, because a push-update carries a filtered subset.
- Only `push-update` is mapped to object rows. A `push-change-update` is stored in the messages table only.
- A subscription whose xpath filter joins targets from different lists gets the columns of the first list only.
- `NetCalyx anydata_strict_validation` is off, as in nl6's interop gate.
- Image tags are pinned by version, not by digest.
- The dashboard queries and the search filters were run against ClickHouse and return data. The rendered UI has not been checked in a browser, so menu names in the ClickStack section may differ slightly.

## Versions

| Component | Version |
| --- | --- |
| nl6 | v0.40.2 |
| NetCalyx | v0.4.0 |
| Kafka, Schema Registry | Confluent Platform 8.3.3 |
| YANG schema registry plugin | yang-kafka-integration v0.0.5 |
| ClickHouse | 26.10 |
| ClickStack, MongoDB | 2.45.0, 9.0.8 |
| OpenTelemetry Collector | 0.165.0 |
| libyang, Python binding | 6.4.13, 5.1.0 |
| Python | 3.14 |

These were the latest releases on 18 November 2026.
libyang is built from source, because Debian 13 packages 3.12.

## Licence

MIT, see [LICENSE](LICENSE).
