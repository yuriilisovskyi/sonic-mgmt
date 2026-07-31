# SONiC SNMP, gRPC (gNMI/gNOI) and RESTCONF Test Strategy

| Rev | Date | Author | Change Description |
|:---:|:-----|:-------|:-------------------|
| 0.1 | 2026-07-31 | Validation team | Initial test strategy covering sonic-mgmt functional tests and Zabbix E2E NMS validation |

## Table of Contents

- [1. Introduction](#1-introduction)
- [2. Goals and Scope](#2-goals-and-scope)
- [3. Feature Descriptions](#3-feature-descriptions)
  - [3.1 SNMP](#31-snmp)
  - [3.2 gRPC / gNMI / gNOI](#32-grpc--gnmi--gnoi)
  - [3.3 RESTCONF](#33-restconf)
  - [3.4 Feature Relationship](#34-feature-relationship)
- [4. Testing Approach Overview](#4-testing-approach-overview)
- [5. Tools](#5-tools)
- [6. Testbed Requirements](#6-testbed-requirements)
- [7. Functional Testing with sonic-mgmt](#7-functional-testing-with-sonic-mgmt)
  - [7.1 SNMP Functional Tests](#71-snmp-functional-tests)
  - [7.2 gRPC / gNMI / gNOI Functional Tests](#72-grpc--gnmi--gnoi-functional-tests)
  - [7.3 RESTCONF Functional Tests](#73-restconf-functional-tests)
- [8. Manual End-to-End Validation with Zabbix](#8-manual-end-to-end-validation-with-zabbix)
- [9. Cross-Feature and Negative Scenarios](#9-cross-feature-and-negative-scenarios)
- [10. Execution Plan and Traceability](#10-execution-plan-and-traceability)
- [11. References](#11-references)

---

## 1. Introduction

SONiC exposes device configuration, operational state, and telemetry through multiple northbound management interfaces. The three interfaces covered by this strategy are:

| Interface | Transport / Protocol | Primary SONiC component | Typical use |
|-----------|----------------------|-------------------------|-------------|
| **SNMP** | UDP/161 (v1/v2c/v3) | `snmp` container (`snmpd` + `sonic_ax_impl` AgentX subagent) | Legacy NMS polling (Zabbix, LibreNMS, etc.) |
| **gRPC / gNMI / gNOI** | gRPC over TLS (default ports 50051 / 8080 depending on image) | `telemetry` / `gnmi` container | Programmatic config, streaming telemetry, ops RPCs |
| **RESTCONF** | HTTPS (default port 443) | `mgmt-framework` container (REST server + Translib) | YANG/OpenConfig model-driven config and state via HTTP |

This document defines a validation strategy that combines:

1. **Automated functional testing** in [sonic-mgmt](https://github.com/sonic-net/sonic-mgmt) (pytest).
2. **Manual end-to-end (E2E) validation** by integrating the DUT with an open-source Network Management System (**Zabbix**).

It is based on official SONiC design documentation (Management Framework, gRPC telemetry, gNMI server design, SNMP schema/HLD) and on the existing test suites under `tests/snmp`, `tests/gnmi`, `tests/telemetry`, and `tests/restapi`.

> **Note on RESTAPI vs RESTCONF:** Existing sonic-mgmt `tests/restapi` covers **sonic-restapi** (HTTPS APIs for VxLAN/VNET dynamic config), which is a separate container from **RESTCONF** in `mgmt-framework`. This strategy treats RESTCONF as the YANG/RFC8040 management interface. sonic-restapi tests remain useful adjacent coverage for HTTPS/cert-based management but do not replace RESTCONF validation.

---

## 2. Goals and Scope

### 2.1 Goals

1. Verify that SNMP, gNMI/gNOI, and RESTCONF correctly expose configuration and operational state from SONiC Redis databases (CONFIG_DB, APPL_DB, STATE_DB, COUNTERS_DB, etc.).
2. Confirm security controls: community/user auth for SNMP; mutual TLS and optional JWT/basic auth for gNMI and RESTCONF.
3. Validate streaming and polling telemetry paths used by modern collectors (gNMI Subscribe SAMPLE / ON_CHANGE / POLL).
4. Prove **operator-facing E2E monitoring** by attaching Zabbix (and optional gNMI bridge) to a live DUT and confirming metrics, inventory, and alerts.
5. Clarify what can run on **VS (KVM) testbeds** versus what requires physical ASICs / sensors / optics.

### 2.2 In Scope

- Feature enablement and service health (`snmp`, `gnmi`/`telemetry`, `mgmt-framework`).
- Read paths for system, interface, LLDP, counters, platform entity, and selected config objects.
- Write paths for gNMI Set and RESTCONF POST/PATCH/PUT/DELETE on supported YANG/DB paths (where image build enables Translib write).
- Certificate lifecycle (install, rotate, revoke) for gNMI/REST servers.
- Zabbix SNMP templates and optional gNMI→metrics bridge for E2E monitoring.

### 2.3 Out of Scope

- Vendor-specific SAI/ASIC performance benchmarking beyond existing counter correctness checks.
- Full RFC8040 RESTCONF notifications (not supported by SONiC Management Framework; clients should use gNMI).
- Replacing existing sonic-restapi VxLAN/VNET test ownership.
- Production NMS HA/scale design beyond a lab Zabbix instance.

---

## 3. Feature Descriptions

### 3.1 SNMP

**Sources:** [SONiC Architecture Wiki](https://github.com/sonic-net/SONiC/wiki/Architecture), [SNMP schema](https://github.com/sonic-net/SONiC/blob/master/doc/snmp/snmp-schema-addition.md), [ConfigDB migration HLD](https://github.com/sonic-net/SONiC/blob/master/doc/snmp/snmp-configdb-migration-hld.md), [Entity MIB extension](https://github.com/sonic-net/SONiC/blob/master/doc/snmp/extension-to-physical-entity-mib.md), [PFC/Queue SNMP HLD](https://github.com/sonic-net/SONiC/wiki/PFC-and-Queue-SNMP-counters-High-Level-Design), [IPv6 SNMP changes](https://github.com/sonic-net/SONiC/blob/master/doc/snmp/snmp-changes-to-support-ipv6.md).

#### Architecture

```
NMS (Zabbix / snmpwalk)
        |  UDP/161
   +----+-----+
   |  snmpd   |  master agent (net-snmp)
   +----+-----+
        | AgentX
   +----+-------------+
   | sonic_ax_impl    |  SONiC AgentX subagent
   +----+-------------+
        |
   Redis: APPL_DB, STATE_DB, COUNTERS_DB, ASIC_DB, CONFIG_DB
```

- **snmpd** receives external polls and forwards MIB requests to the SONiC subagent.
- **sonic_ax_impl** caches DB-derived MIB state (refresh interval typically &lt; 60s) and serves IF-MIB, LLDP-MIB, ENTITY-MIB, SNMPv2-MIB, Cisco PFC/QoS MIBs, HOST-RESOURCES/UCD memory/CPU, etc.
- Configuration lives in ConfigDB tables `SNMP`, `SNMP_COMMUNITY`, `SNMP_USER` (migrated from legacy `/etc/sonic/snmp.yml`), plus `SNMP_ACL` for access control.

#### Key capabilities to validate

| Area | Details |
|------|---------|
| Service | Container/feature `snmp` fully started; agent listens on IPv4/IPv6 |
| Auth | SNMPv2c RO/RW communities; SNMPv3 users (noAuthNoPriv / AuthNoPriv / Priv) |
| System MIB | sysDescr, sysUpTime, sysContact, sysName, sysLocation |
| Interfaces | ifTable/ifXTable including management ports; oper/admin status; counters |
| LLDP | Neighbor information consistency with `show lldp` |
| Platform | Fans, PSU, thermals, transceivers via ENTITY / Entity Sensor MIB |
| QoS | PFC and queue counters (ciscoPfcExtMIB / ciscoSwitchQosMIB) |
| Reachability | Poll via mgmt IP, loopback, link-local IPv6 where supported |

### 3.2 gRPC / gNMI / gNOI

**Sources:** [gRPC telemetry](https://github.com/sonic-net/SONiC/blob/master/doc/system-telemetry/grpc_telemetry.md), [gNMI Server Interface Design](https://github.com/sonic-net/SONiC/blob/master/doc/mgmt/gnmi/SONiC_GNMI_Server_Interface_Design.md), [gNMI Subscribe for YANG](https://github.com/sonic-net/SONiC/blob/master/doc/mgmt/gnmi/gNMI_Subscription_for_YangData.md), [Management Framework](https://github.com/sonic-net/SONiC/blob/master/doc/mgmt/Management%20Framework.md).

#### Architecture

SONiC implements telemetry largely on **gNMI over gRPC**. Newer images use the `gnmi` / `docker-sonic-gnmi` container; older branches use `telemetry` / `docker-sonic-telemetry`.

```
gNMI client (gnmi_cli / pygnmi / gnmic / sonic-mgmt PTF helpers)
        |  gRPC + TLS (mTLS preferred)
   +----+------------------+
   | gNMI / gNOI server    |
   +----+------------------+
        |
   +----+--------+     +------------------+
   | DB paths    |     | Translib / YANG  |
   | (sonic_db)  |     | (OpenConfig etc) |
   +----+--------+     +--------+---------+
        |                       |
   CONFIG/APPL/STATE/COUNTERS   Redis via App modules
```

**gNMI RPCs:** Capabilities, Get, Set, Subscribe (ONCE / POLL / STREAM SAMPLE / STREAM ON_CHANGE).

**Path models:**

1. **Native DB paths** — `target` = `CONFIG_DB` | `APPL_DB` | `COUNTERS_DB` | `STATE_DB` | `OTHERS`, with TABLE/KEY/FIELD hierarchy.
2. **YANG / OpenConfig paths** — via Translib (shared with RESTCONF).
3. **Virtual paths** — telemetry-translated aggregates (e.g. named port counters instead of OID keys).

**gNOI** extends the same gRPC server with operational RPCs (System Time, Reboot, KillProcess, OS Activate/Verify, etc.).

**ConfigDB (illustrative):**

```json
{
  "GNMI": {
    "certs": {
      "ca_crt": "/etc/sonic/telemetry/dsmsroot.cer",
      "server_crt": "/etc/sonic/telemetry/streamingtelemetryserver.cer",
      "server_key": "/etc/sonic/telemetry/streamingtelemetryserver.key"
    },
    "gnmi": {
      "client_auth": "true",
      "log_level": "2",
      "port": "50051"
    }
  }
}
```

### 3.3 RESTCONF

**Sources:** [Management Framework](https://github.com/sonic-net/SONiC/blob/master/doc/mgmt/Management%20Framework.md), [sonic-mgmt-framework](https://github.com/sonic-net/sonic-mgmt-framework).

#### Architecture

RESTCONF runs inside the **`mgmt-framework`** container. The Go REST server links statically to **Translib**, which maps YANG/OpenConfig (and SONiC YANG) to Redis ABNF schema with CVL validation.

```
RESTCONF client (curl / Postman / Ansible / NMS)
        |  HTTPS :443  (application/yang-data+json)
   +----+------------------+
   | REST server           |
   +----+------------------+
        | Translib APIs
   +----+------------------+
   | App modules + CVL     |
   +----+------------------+
        |
      Redis ConfigDB / State
```

| HTTP | Translib | Purpose |
|------|----------|---------|
| GET / HEAD | Get | Read config/state |
| POST | Create / Action | Create resource or YANG RPC |
| PATCH | Update | Merge update |
| PUT | Replace | Full replace |
| DELETE | Delete | Remove resource |
| OPTIONS | — | Advertise allowed methods |

**Discovery endpoints (RFC8040):**

- `GET /.well-known/host-meta` → RESTCONF root `/restconf`
- `GET /restconf/yang-library-version`
- `GET /restconf/data/ietf-yang-library:modules-state`
- `GET /restconf/data/ietf-restconf-monitoring:restconf-state/capabilities`
- `GET /models/yang/{filename}`

**Auth modes:** HTTP Basic, JWT bearer, TLS client certs, or combinations (ConfigDB `REST_SERVER` table). RESTCONF notifications are **not** supported; use gNMI Subscribe for eventing.

### 3.4 Feature Relationship

```
                 +------------------+
                 |   YANG / DB      |
                 |   models         |
                 +--------+---------+
                          |
          +---------------+---------------+
          |               |               |
     +----v----+    +-----v-----+   +-----v-----+
     | RESTCONF|    | gNMI/gNOI |   |   SNMP    |
     | (HTTPS) |    |  (gRPC)   |   |  (UDP)    |
     +----+----+    +-----+-----+   +-----+-----+
          |               |               |
          +-------+-------+               |
                  |                       |
            Translib / Redis        AgentX + Redis cache
                  |                       |
                  +-----------+-----------+
                              |
                         SONiC DUT
                              |
                    +---------v---------+
                    | Zabbix / NMS E2E  |
                    +-------------------+
```

All three interfaces ultimately surface the same underlying switch state; E2E validation should cross-check that NMS-visible values match CLI/`redis-cli` ground truth.

---

## 4. Testing Approach Overview

| Layer | Method | Owner tooling | Outcome |
|-------|--------|---------------|---------|
| L1 Service readiness | Automated | sonic-mgmt fixtures / `show feature status` | Containers up, ports listening |
| L2 Protocol correctness | Automated | pytest in `tests/snmp`, `tests/gnmi`, `tests/telemetry`; new `tests/restconf` | MIB/RPC/REST responses match DUT state |
| L3 Security | Automated + manual | Cert fixtures, community/user config | Unauthorized access denied; cert rotate works |
| L4 Operator E2E | Manual | Zabbix + optional gnmic bridge | Dashboards, inventory, alerts usable |
| L5 Regression | CI | VS nightly + physical hardware nightly | Continuous coverage |

**Two complementary tracks:**

1. **Track A — sonic-mgmt functional:** Fast, repeatable, CI-friendly; asserts protocol and data correctness.
2. **Track B — Zabbix E2E:** Confirms real NMS workflows (discovery, polling intervals, triggers, maps) against the same DUT.

---

## 5. Tools

| Tool | Role |
|------|------|
| **sonic-mgmt** (pytest + ansible) | Primary automated test harness |
| **PTF host** | Runs gnxi clients (`gnmi_get`, `gnmi_set`, `gnmi_cli`), hosts cert helpers |
| **net-snmp** (`snmpget`/`snmpwalk`/`snmpbulkwalk`) | SNMP probes from localhost / neighbor VM |
| **openssl** | Generate CA/server/client certs for gNMI and RESTCONF |
| **curl / httpie / Postman** | Manual RESTCONF calls |
| **gnmic / pygnmi** | Interactive gNMI Get/Subscribe for lab and Zabbix bridge |
| **Zabbix Server + Agent/Proxy** | Open-source NMS for E2E SNMP (and optional HTTP/Prometheus items) |
| **Zabbix network device SNMP templates** | Baseline IF-MIB / HOST-RESOURCES / SNMPv2-MIB monitoring |
| **redis-cli / sonic-db-cli / show CLI** | Ground-truth comparison on DUT |
| **Wireshark / tcpdump** | Optional packet-level debugging (SNMP SRC/DST, TLS handshake) |

---

## 6. Testbed Requirements

### 6.1 Recommended topologies

| Topology | SNMP | gNMI/gNOI | RESTCONF | Notes |
|----------|------|-----------|----------|-------|
| **VS / KVM `t0` or `t1`** | Most MIB tests | Most Get/Set/Subscribe + Capabilities | Discovery + basic CRUD if feature enabled | Preferred for CI and early bring-up |
| **Physical `t0`/`t1`** | Full suite including ENTITY sensors, PSU, optics | Full suite + queue buffer where ASIC supports | Full YANG path suite | Required for platform MIBs and realistic counters |
| **Multi-ASIC / T2** | Partial (see skips) | ConfigDB Set often skipped | Validate per-ASIC caveats | Follow conditional marks |

### 6.2 VS (KVM) suitability

| Feature area | Runs on VS? | Additional notes |
|--------------|-------------|------------------|
| SNMP v2c system/interfaces/memory/CPU/LLDP/FDB/default-route | **Yes** | Needs `snmp` feature enabled and community from lab inventory (`snmp_rocommunity`) |
| SNMP physical entity (fan/PSU/thermal/transceiver) | **No** | Marked skip: `asic_type in ['vs']` — requires real hardware |
| SNMP queue counters | **Limited** | Known KVM issues (`#14007`); prefer physical |
| SNMP link-local IPv6 | **Flaky on VS** | xfail on VS (`#15081`) |
| gNMI Capabilities / Get / Subscribe / ConfigDB Set | **Yes** | Needs `gnmi` or `telemetry` docker image in the build; TLS certs applied by fixtures |
| gNMI events / eventd | **Flaky on VS** | xfail historically (`sonic-buildimage#19943`) |
| gNOI reboot / OS activate | **Caution** | Use dedicated lab DUT; warm reboot topo-restricted |
| RESTCONF (mgmt-framework) | **Yes if feature present** | Ensure `mgmt-framework` enabled; install server/client certs or basic auth; **no dedicated sonic-mgmt suite today — gap to close** |
| Zabbix E2E | **Yes** | Zabbix can poll VS DUT mgmt IP; platform items (fans/PSU) will be empty/N/A |

### 6.3 Common DUT configuration prerequisites

```bash
# Feature enablement (names vary by branch)
sudo config feature state snmp enabled
sudo config feature state gnmi enabled          # or: telemetry enabled
sudo config feature state mgmt-framework enabled

# SNMP (ConfigDB / CLI)
sudo config snmp community add public ro
sudo config snmp location add lab-rack-1

# gNMI certs (paths must match GNMI|certs in ConfigDB)
# RESTCONF: REST_SERVER table — port, client_auth, cert paths
```

**Inventory / ansible:** Lab group vars typically define `snmp_rocommunity` and `snmp_location` (see `ansible/group_vars/lab/lab.yml`). VS setup follows [README.testbed.VsSetup.md](../testbed/README.testbed.VsSetup.md).

### 6.4 Additional lab components for E2E

| Component | Requirement |
|-----------|-------------|
| Zabbix Server | VM/container with IP reachability to DUT management network |
| Management network | L3 path between Zabbix and DUT `eth0`/mgmt VRF (if mgmt VRF used, ensure Zabbix is in that VRF or routed) |
| TLS PKI | Shared lab CA for gNMI/RESTCONF; import CA into clients |
| Optional gnmic | Host that dials DUT `:50051`/`:8080` and exports Prometheus metrics for Zabbix HTTP agent items |
| Firewall | Allow UDP/161 (SNMP), TCP/443 (RESTCONF), TCP/50051 or 8080 (gNMI) |

### 6.5 Image / feature matrix checks

Before a campaign, confirm image contents:

```bash
docker images | grep -E 'snmp|gnmi|telemetry|mgmt-framework|restapi'
show feature status
```

Skip or gate tests when containers are absent (existing telemetry fixture already skips if neither `docker-sonic-gnmi` nor `docker-sonic-telemetry` is present).

---

## 7. Functional Testing with sonic-mgmt

### 7.1 SNMP Functional Tests

**Existing location:** `tests/snmp/`  
**Existing test plans:** `docs/testplan/SNMP-*.md`

#### Setup

- Module fixture ensures `snmp` service is fully started, applies community/location from `snmp.yml` / ConfigDB, and rolls back via checkpoint.
- Poller typically runs from sonic-mgmt localhost (or neighbor VM for loopback-path tests) using inventory credentials.

#### Scenario catalog

| ID | Scenario | Steps (summary) | Expected | VS |
|----|----------|-----------------|----------|----|
| SNMP-01 | Service readiness | `is_service_fully_started("snmp")`; UDP 161 reachable | Pass | Yes |
| SNMP-02 | SNMPv2-MIB | snmpwalk system group; compare to `show version` / hostname / location | Values match | Yes |
| SNMP-03 | IF-MIB / ifXTable | Poll all interfaces; compare admin/oper/MTU/speed/alias to Redis/CLI | Match front-panel + mgmt | Yes |
| SNMP-04 | Mgmt interface MIB | Specifically validate management port indexing | Present with sensible counters (may be 0) | Yes |
| SNMP-05 | LLDP-MIB | Compare SNMP neighbors to `show lldp table` | Consistent | Yes |
| SNMP-06 | FDB via SNMP | Inject tagged traffic; verify FDB entries visible | Entry present | Yes |
| SNMP-07 | Memory / swap / CPU | Compare HOST-RESOURCES/UCD MIBs to `/proc` within tolerance | Within threshold | Yes |
| SNMP-08 | Default route | SNMP IP-FORWARD / route objects vs DUT default route | Match | Yes* |
| SNMP-09 | Loopback poll | SNMP query targeting Loopback from neighbor VM | Response SRC IP correct | Yes* |
| SNMP-10 | Link-local IPv6 | Poll via fe80:: on mgmt | Response received | Limited |
| SNMP-11 | PFC counters | Walk ciscoPfcExtMIB; compare COUNTERS_DB | Match | Physical preferred |
| SNMP-12 | Queue counters | Walk QoS queue MIB | Match | Physical preferred |
| SNMP-13 | PSU / fan / thermal / optics | ENTITY-MIB hierarchy and sensors | Match platform API | **No (VS skip)** |
| SNMP-14 | SNMPv3 auth | Configure SNMP_USER; poll with authPriv | Success with good creds; fail with bad | Yes |
| SNMP-15 | SNMP ACL | Deny client via SNMP_ACL; confirm timeout/reject | Denied | Yes |

\* Skip on `standalone` / `backend` topologies where routes or neighbor VMs are absent (see conditional marks).

#### Example execution

```bash
cd /path/to/sonic-mgmt/tests
./run_tests.sh -n <tb_name> -d <dut> -u -s snmp
# or focused:
pytest snmp/test_snmp_v2mib.py snmp/test_snmp_interfaces.py -vv
```

### 7.2 gRPC / gNMI / gNOI Functional Tests

**Existing locations:** `tests/gnmi/`, `tests/telemetry/`

#### Setup

- Ensure gnmi/telemetry container image exists and feature is enabled.
- Fixtures create CA/server/client certificates, push them to DUT, update ConfigDB `GNMI` certs, wait for server start.
- NTP sync may be required on non-KVM platforms for cert validity windows.
- Clients run from PTF (`gnxi_path`) or localhost gRPC stubs (gNOI protos under `tests/gnmi/protos`).

#### Scenario catalog

| ID | Scenario | Steps (summary) | Expected | VS |
|----|----------|-----------------|----------|----|
| GNMI-01 | Capabilities | Capabilities RPC | Model list / encodings returned | Yes |
| GNMI-02 | Capabilities auth negative | Invalid/revoked client cert or bad CN | UNAUTHENTICATED / PERMISSION_DENIED | Yes |
| GNMI-03 | Get CONFIG_DB | Get path e.g. DEVICE_METADATA / VLAN | JSON matches `sonic-db-cli` | Yes |
| GNMI-04 | Get COUNTERS_DB | Port name map + counters / virtual paths | Non-empty structured data | Yes |
| GNMI-05 | Get APPL_DB | Selected APPL_DB tables | Match Redis | Yes |
| GNMI-06 | Set incremental | gNMI Set update on CONFIG_DB object; verify applied | Config present; recoverable | Yes† |
| GNMI-07 | Set full replace | Replace subtree; verify | Applied atomically or rejected cleanly | Yes† |
| GNMI-08 | Subscribe POLL | Poll subscription on config/counters | Periodic updates | Yes |
| GNMI-09 | Subscribe SAMPLE | Streaming sample on counters | Samples at interval | Yes |
| GNMI-10 | Subscribe ON_CHANGE | Change config; observe update | Event received | Yes |
| GNMI-11 | Telemetry enabled-by-default / ConfigDB params | Feature and GNMI table sanity | Consistent with design | Yes |
| GNMI-12 | Virtual path / sysuptime / osbuild | OTHERS/virtual paths | Sensible values | Yes |
| GNMI-13 | Cert rotation | Delete/add/rotate server cert; client still works after fix | Server stays healthy; traffic recovers | Yes |
| GNMI-14 | Memory spike under subscribe | Stress subscribe; watch container memory | No OOM / unbounded growth | Yes |
| GNMI-15 | Events (eventd) | Trigger BGP/host/swss events; validate YANG | Events received | Flaky on VS |
| GNOI-01 | System Time | gNOI Time RPC | Matches DUT time | Yes |
| GNOI-02 | KillProcess | Kill/restart allowed process | Process lifecycle as requested | Lab only |
| GNOI-03 | Reboot cold/warm | gNOI Reboot | DUT reboots; recovers | Physical/lab; topo limits |
| GNOI-04 | OS Verify/Activate | Image verify / activate | Valid image OK; invalid rejected | Lab |

† ConfigDB write tests are skipped on multi-ASIC / T2 in conditional marks.

#### Example execution

```bash
pytest gnmi/test_gnmi.py gnmi/test_gnmi_configdb.py -vv
pytest telemetry/test_telemetry.py -vv
```

### 7.3 RESTCONF Functional Tests

**Gap:** There is **no first-class `tests/restconf/` suite** in sonic-mgmt today. Management Framework RESTCONF must be covered by a new module (recommended path below). Existing `tests/restapi/` validates **sonic-restapi** (VxLAN/VNET), mostly Mellanox-scoped — use only as a pattern for cert setup and HTTPS client helpers.

#### Proposed module layout

```
tests/restconf/
  conftest.py          # enable mgmt-framework, certs/basic auth, URL builder
  restconf_utils.py    # GET/PATCH helpers, yang-data+json headers
  test_restconf_discovery.py
  test_restconf_yang_library.py
  test_restconf_interfaces.py
  test_restconf_system.py
  test_restconf_auth.py
  test_restconf_error_handling.py
```

#### Setup

1. Confirm `mgmt-framework` feature enabled and container running.
2. Configure `REST_SERVER` (port 443, `client_auth`, cert paths) or use basic auth credentials tied to SONiC AAA.
3. From sonic-mgmt, call HTTPS endpoints with `Accept: application/yang-data+json` and `Content-Type: application/yang-data+json`.

#### Scenario catalog

| ID | Scenario | Steps (summary) | Expected | VS |
|----|----------|-----------------|----------|----|
| RC-01 | Feature/container up | `show feature status mgmt-framework`; docker ps | Running | Yes |
| RC-02 | Discovery host-meta | GET `/.well-known/host-meta` | Root `/restconf` | Yes |
| RC-03 | YANG library | GET modules-state; download a YANG file | Modules listed; file retrieved | Yes |
| RC-04 | Capabilities | GET restconf-state/capabilities | Capability URNs present | Yes |
| RC-05 | GET OpenConfig interface | GET interface state/config for EthernetX | Matches `show interfaces` | Yes |
| RC-06 | GET system | GET openconfig-system / hostname, memory | Matches CLI | Yes |
| RC-07 | PATCH interface description | PATCH then GET | Persisted in ConfigDB | Yes‡ |
| RC-08 | PUT replace / DELETE | Create leaf/list entry; delete | CRUD cycle clean | Yes‡ |
| RC-09 | OPTIONS / Allow headers | OPTIONS on data resource | Allow lists methods; Accept-Patch if PATCH supported | Yes |
| RC-10 | Auth positive/negative | Basic / JWT / client cert good & bad | 200 vs 401/403 | Yes |
| RC-11 | Malformed payload | Send invalid JSON / schema-breaking body | RESTCONF error-tag JSON (4xx) | Yes |
| RC-12 | Concurrent GETs | Parallel reads while one write holds lock | Reads succeed; write serialized | Yes |
| RC-13 | Parity with gNMI | Same OpenConfig path via RESTCONF GET and gNMI Get | Equivalent data | Yes |
| RC-14 | Mgmt VRF | With management VRF, RESTCONF reachable only as designed | Consistent with VRF policy | If topo supports |

‡ Requires image with Translib write enabled for the targeted models.

#### Interim manual checklist (until automation lands)

```bash
curl -k -u admin:<pass> -H 'Accept: application/yang-data+json' \
  https://<dut-mgmt-ip>/.well-known/host-meta

curl -k -u admin:<pass> -H 'Accept: application/yang-data+json' \
  https://<dut-mgmt-ip>/restconf/data/ietf-yang-library:modules-state
```

---

## 8. Manual End-to-End Validation with Zabbix

### 8.1 Objectives

Demonstrate that an operator can manage/monitor a SONiC DUT using an open-source NMS:

1. Auto-discover or manually add the DUT.
2. Collect SNMP inventory and interface metrics.
3. (Optional) Ingest gNMI telemetry via a collector bridge.
4. Raise alerts on interface down, high CPU/memory, or loss of SNMP availability.
5. Cross-check Zabbix values against sonic-mgmt/CLI ground truth.

### 8.2 Lab topology

```
+------------------+         mgmt network          +------------------+
| Zabbix Server    |-------------------------------| SONiC DUT        |
| - SNMP poller    |         UDP/161               | snmp             |
| - HTTP items     |         TCP/443               | mgmt-framework   |
| - (optional)     |         TCP/50051|8080        | gnmi/telemetry   |
|   Prometheus     |<---+                          +------------------+
|   scrape         |    |
+------------------+    |   +------------------+
                        +---| gnmic / Telegraf |
                            | (gNMI dial-in)   |
                            +------------------+
```

VS DUT is sufficient for SNMP reachability, IF-MIB, and system MIBs. Use physical DUT when validating ENTITY sensors and realistic traffic counters.

### 8.3 Zabbix + SNMP procedure

| Step | Action | Pass criteria |
|------|--------|---------------|
| 1 | Enable SNMP on DUT; set RO community matching Zabbix macro `{$SNMP_COMMUNITY}` | `snmpwalk -v2c -c public <dut> system` works from Zabbix host |
| 2 | Create Zabbix host with DUT mgmt IP; link **Network device SNMP** / generic IF-MIB templates | Host becomes Available (green SNMP icon) |
| 3 | Wait discovery of network interfaces | Interfaces appear as items; oper status maps correctly |
| 4 | Compare selected items to DUT | ifOperStatus, ifHCInOctets delta, sysDescr match CLI/SNMP from sonic-mgmt |
| 5 | Shut an interface on DUT | Zabbix trigger fires within configured interval |
| 6 | Simulate SNMP outage (stop `snmp` feature) | SNMP availability trigger fires; restore recovers |
| 7 | (Physical) Validate PSU/fan items if custom SONiC template added | Values match ENTITY-MIB |

**Suggested Zabbix items for a SONiC profile:**

- SNMPv2-MIB: sysDescr, sysUpTime, sysName, sysLocation  
- IF-MIB: ifOperStatus, ifAdminStatus, ifHCInOctets, ifHCOutOctets, ifInErrors, ifOutErrors  
- HOST-RESOURCES / UCD: memory used, CPU load  
- Optional custom OIDs: PFC/queue counters for DC fabrics  

### 8.4 Zabbix + gNMI procedure (bridge)

Zabbix does not natively speak gNMI. Recommended pattern:

1. Run **gnmic** (or Telegraf gNMI input) on a lab host with client certs.
2. Subscribe to paths such as:
   - COUNTERS_DB port counters / virtual interface paths  
   - OpenConfig `/interfaces/interface[name=*]/state`  
   - CONFIG_DB `DEVICE_METADATA`  
3. Export to **Prometheus** remote-write/exposition.
4. In Zabbix, use **HTTP agent** or **Prometheus** data collection to graph the same metrics.
5. Validate that a counter increment seen in `redis-cli -n 2` appears in Zabbix within the scrape interval.

| Step | Action | Pass criteria |
|------|--------|---------------|
| 1 | mTLS session from gnmic to DUT | Subscribe established |
| 2 | SAMPLE subscribe 10s on interface counters | Continuous updates |
| 3 | ON_CHANGE on admin status; shut/no-shut interface | Event reflected in bridge metrics |
| 4 | Break cert / wrong CN | Session fails; Zabbix alert on scrape failure |
| 5 | Cert rotate on DUT | After update, metrics resume without DUT reboot |

### 8.5 Zabbix + RESTCONF procedure (optional)

| Step | Action | Pass criteria |
|------|--------|---------------|
| 1 | Zabbix HTTP agent GET capabilities / modules-state | HTTP 200; JSON parseable |
| 2 | Item for interface oper-status via RESTCONF | Matches SNMP item for same port |
| 3 | Unauthorized request (bad password) | 401; optional security trigger |

Use RESTCONF in Zabbix primarily for **configuration audit / state spot-checks**, not high-rate telemetry (prefer gNMI).

### 8.6 E2E acceptance checklist

- [ ] DUT visible in Zabbix with SNMP Available  
- [ ] ≥95% of expected interfaces discovered  
- [ ] sysDescr contains SONiC software version string  
- [ ] Interface down alert verified end-to-end  
- [ ] SNMP stop/start alert verified  
- [ ] (Optional) gNMI-bridged counter correlates with SNMP ifHCInOctets within tolerance  
- [ ] (Optional) RESTCONF GET parity sample documented  
- [ ] Results attached to test report (screenshots + OID/path list)

---

## 9. Cross-Feature and Negative Scenarios

| ID | Scenario | Purpose |
|----|----------|---------|
| XF-01 | Same interface oper-status via SNMP, gNMI, RESTCONF, CLI | Multi-NBI consistency |
| XF-02 | Config change via RESTCONF visible via gNMI Get and `show` | Write-path coherence through Translib |
| XF-03 | Config change via gNMI Set visible via RESTCONF GET | Bidirectional NBI consistency |
| XF-04 | SNMP community remove while Zabbix polling | Secure failure + recover |
| XF-05 | Disable `gnmi` feature under active subscribe | Client error; no DUT crash; re-enable recovers |
| XF-06 | Disable `mgmt-framework` | RESTCONF connection refused; SNMP/gNMI unaffected |
| XF-07 | Mgmt ACL / control-plane ACL blocking 161/443/gNMI port | Confirm ACL enforcement |
| XF-08 | Warm/fast reboot | SNMP and gNMI services restore; Zabbix availability recovers |

---

## 10. Execution Plan and Traceability

### 10.1 Suggested campaign order

1. **Bring-up on VS t0:** SNMP-01..07, GNMI-01..12, RC-01..06 (manual or new automation).  
2. **Security pass:** SNMP-14/15, GNMI-02/13, RC-10/11.  
3. **Zabbix E2E on VS:** Section 8.3 (+ 8.4 if certs ready).  
4. **Physical DUT regression:** SNMP-11..13, queue/PFC, entity sensors, traffic-backed counters.  
5. **Destructive/ops:** GNOI reboot/OS on dedicated lab box; XF-05..08.

### 10.2 Mapping to existing sonic-mgmt files

| Area | Paths |
|------|-------|
| SNMP | `tests/snmp/test_snmp_*.py`, `docs/testplan/SNMP-*.md` |
| gNMI/gNOI | `tests/gnmi/test_gnmi*.py`, `tests/gnmi/test_gnoi_*.py` |
| Telemetry streaming | `tests/telemetry/test_telemetry*.py`, `tests/telemetry/test_events.py` |
| RESTAPI (adjacent) | `tests/restapi/` (not RESTCONF) |
| RESTCONF (proposed) | `tests/restconf/` (to be added) |
| Conditional skips | `tests/common/plugins/conditional_mark/tests_mark_conditions.yaml` |

### 10.3 Exit criteria

- All applicable VS-eligible automated cases **pass** on target branch (e.g. 202505).  
- Physical-only cases executed on at least one hardware SKU or explicitly waived.  
- Zabbix E2E checklist completed and archived.  
- Known failures linked to GitHub issues (follow existing xfail/skip pattern).  
- Any new RESTCONF automation merged with VS marks and feature-gate skips when `mgmt-framework` is absent.

---

## 11. References

### SONiC design documentation

1. [Management Framework](https://github.com/sonic-net/SONiC/blob/master/doc/mgmt/Management%20Framework.md) — RESTCONF, Translib, NBIs  
2. [SONiC gRPC data telemetry](https://github.com/sonic-net/SONiC/blob/master/doc/system-telemetry/grpc_telemetry.md)  
3. [gNMI Server Interface Design](https://github.com/sonic-net/SONiC/blob/master/doc/mgmt/gnmi/SONiC_GNMI_Server_Interface_Design.md)  
4. [gNMI Subscription for YANG Data](https://github.com/sonic-net/SONiC/blob/master/doc/mgmt/gnmi/gNMI_Subscription_for_YangData.md)  
5. [SNMP schema addition](https://github.com/sonic-net/SONiC/blob/master/doc/snmp/snmp-schema-addition.md)  
6. [SNMP ConfigDB migration HLD](https://github.com/sonic-net/SONiC/blob/master/doc/snmp/snmp-configdb-migration-hld.md)  
7. [Entity MIB extension](https://github.com/sonic-net/SONiC/blob/master/doc/snmp/extension-to-physical-entity-mib.md)  
8. [SNMP IPv6 changes](https://github.com/sonic-net/SONiC/blob/master/doc/snmp/snmp-changes-to-support-ipv6.md)  
9. [Architecture Wiki — SNMP container](https://github.com/sonic-net/SONiC/wiki/Architecture)  
10. [PFC and Queue SNMP counters HLD](https://github.com/sonic-net/SONiC/wiki/PFC-and-Queue-SNMP-counters-High-Level-Design)  
11. [sonic-mgmt-framework](https://github.com/sonic-net/sonic-mgmt-framework)  
12. [sonic-snmpagent](https://github.com/sonic-net/sonic-snmpagent)

### sonic-mgmt / testbed

13. [sonic-mgmt tests](https://github.com/sonic-net/sonic-mgmt/tree/master/tests)  
14. [VS testbed setup](../testbed/README.testbed.VsSetup.md)  
15. Existing plans: `SNMP-v2mib-test-plan.md`, `SNMP-interfaces-test-plan.md`, `SNMP-memory-test-plan.md`

### External specs and NMS

16. [gNMI specification](https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-specification.md)  
17. [RFC8040 RESTCONF](https://tools.ietf.org/html/rfc8040)  
18. [Zabbix network device SNMP templates](https://www.zabbix.com/documentation/current/en/manual/config/templates_out_of_the_box/network_devices)  
19. [gnmic](https://gnmic.openconfig.net/) — gNMI collector often used with SONiC lab stacks  
