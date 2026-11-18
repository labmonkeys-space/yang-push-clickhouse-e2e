# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT

SHELL := /bin/bash
DEVICES ?= 3
START_IP ?= 10.42.0.1
WAIT ?= 150
NL6 := http://127.0.0.1:8080
CH := docker compose exec -T clickhouse clickhouse-client --user yang --password yang -d yang

.DEFAULT_GOAL := help
.PHONY: help check keys up devices wait link-down verify e2e ui sql logs down

## help: List the targets
help:
	@sed -n 's/^## //p' $(MAKEFILE_LIST)

## check: Validate the Compose file and compile the Python sources
check:
	docker compose config -q
	python3 -m py_compile consumer/consumer.py consumer/report.py

# NetCalyx logs in to the simulated devices over NETCONF with this key.
keys/id_ed25519:
	mkdir -p keys
	ssh-keygen -q -t ed25519 -N '' -C yang-push-clickhouse-e2e -f keys/id_ed25519
	cp keys/id_ed25519.pub keys/authorized_keys
	chmod 644 keys/id_ed25519

keys: keys/id_ed25519

# NetCalyx decodes UDP-Notif as soon as its socket is open, but drops what
# arrives before its pipeline is up. Devices must not start sending before
# NetCalyx has registered its default schema and exported that counter.
## up: Build and start the chain, without devices
up: keys
	docker compose up -d --build
	@echo "waiting for NetCalyx to be ready ..."
	@for i in $$(seq 1 60); do \
	  curl -s http://127.0.0.1:8889/metrics | grep -q '^netcalyx_collector_kafka_yang_schema_registry_registrations' && exit 0; \
	  sleep 2; \
	done; echo "NetCalyx did not become ready"; exit 1

## devices: Create DEVICES simulated HUAWEI NE 8000 that send YANG-Push over UDP-Notif
devices:
	curl -sf -X POST $(NL6)/api/v1/devices -H 'Content-Type: application/json' \
	  -d '{"start_ip":"$(START_IP)","device_count":$(DEVICES),"resource_file":"huawei_ne8000.json","yang_push":{"collector":"10.254.0.1:10000"}}'
	@echo

## wait: Let the subscriptions run for WAIT seconds
wait:
	sleep $(WAIT)

## link-down: Take interface 3 of the first device down and up, for an on-change update
link-down:
	curl -sf -X POST $(NL6)/api/v1/devices/$(START_IP)/interfaces/3/oper-status -H 'Content-Type: application/json' -d '{"status":"DOWN"}'
	@echo; sleep 5
	curl -sf -X POST $(NL6)/api/v1/devices/$(START_IP)/interfaces/3/oper-status -H 'Content-Type: application/json' -d '{"status":"UP"}'
	@echo

## verify: Follow the message count from nl6 to ClickHouse and print the result table
verify:
	docker compose exec -T consumer python /app/report.py

## e2e: Full run on a clean stack: up, devices, wait, link-down, verify
e2e: down up devices wait link-down
	sleep 15
	$(MAKE) verify

## ui: Print the ClickStack address and login
ui:
	@echo "http://localhost:8090  user demo@example.com  password YangPush-e2e-1"

## sql: Open a ClickHouse client, or run Q="SELECT ..."
sql:
	@if [ -n "$(Q)" ]; then $(CH) -q "$(Q)"; else docker compose exec clickhouse clickhouse-client --user yang --password yang -d yang; fi

## logs: Follow the NetCalyx and consumer logs
logs:
	docker compose logs -f netcalyx consumer

## down: Stop the chain and delete its data
down:
	docker compose down -v
