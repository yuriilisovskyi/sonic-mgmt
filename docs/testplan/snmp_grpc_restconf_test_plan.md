# SNMP, gRPC, and RESTCONF Test Plan

## Overview

This plan validates SONiC northbound management interfaces through `sonic-mgmt`:

* SNMP: agent availability, protocol access, and MIB data correctness against Redis, FRR, Linux, and platform state
* gRPC: gNMI Capabilities/Get/Set/Subscribe and gNOI operational RPCs
* RESTCONF: HTTPS discovery, CRUD, RPC, query parameters, authentication, and error handling

The suite is functional. Sustained scale, performance, and full protocol-conformance certification are out of scope, except for small bulk or concurrency checks that expose torn state or resource leaks.

## Scope

In scope:

* SNMPv2c, and SNMPv3 when the image supports it
* Implemented standard and vendor MIB objects used for identity, interfaces, counters, LLDP, routes, ARP, FDB, entities, sensors, PSU, queues, PFC, BGP peer state, and CPU/memory utilization
* gNMI native DB and YANG/OpenConfig paths; gNOI System, File, and OS; optional FactoryReset, Healthz, Containerz, Debug, ORAS, SonicService/JWT, and gNSI when the image advertises them
* Management Framework RESTCONF on `/restconf` (not the legacy `restapi` `/v1` VNET/VXLAN API)

Out of scope:

* Claiming functional coverage for MIB placeholders that return empty/`0`/fixed values
* Treating documented `Unimplemented` gNOI methods as pass
* RESTCONF notifications (not supported; monitoring uses gNMI)

## Objectives

1. Confirm each service starts, listens only on configured addresses and ports, and stays healthy after valid and invalid requests.
2. Confirm advertised models, encodings, operations, and discovery data.
3. Compare returned values with CONFIG_DB, APPL_DB, STATE_DB, COUNTERS_DB, ASIC_DB, Linux, FRR, CLI, or platform APIs.
4. Confirm create, update, replace, delete, and RPC side effects for writable interfaces.
5. Confirm authentication, authorization, TLS identity, malformed input, and stable error mapping.
6. Wait for source-of-truth and protocol output to converge instead of using fixed sleeps.
7. Restore configuration with checkpoint/rollback and suite cleanup.
8. Cover single-ASIC and multi-ASIC behavior where namespace aggregation is part of the feature.

## Related DUT configuration

```
config snmp community add <community> ro|rw
config snmp location add <location>
config snmp contact add <contact>
config snmp user add ...                 # SNMPv3, if supported
config snmpagentaddress add ...

config interface <startup|shutdown|mtu|description> ...
config vlan add|del ...
config save -y

# gNMI/gNOI: FEATURE gnmi or telemetry enabled; GNMI/TELEMETRY tables;
# GNMI_CLIENT_CERT|<CN> roles; certificates under /etc/sonic/telemetry/

# RESTCONF: FEATURE mgmt-framework enabled
# REST_SERVER|default: port, client_auth (user|cert|none|jwt as supported)
```

## Testing process

Automation uses `pytest` in `sonic-mgmt`.

1. Record image, platform/HWSKU, ASIC count, feature/container status, listening ports, and topology.
2. Create a checkpoint and collect baseline protocol output plus Redis/CLI/platform data.
3. Provision temporary communities, users, certificates, roles, and protocol configuration.
4. Run positive, transition, negative, and recovery cases.
5. Poll the authoritative state source and protocol output until both match.
6. Check service logs for panic, traceback, credential leakage, and unexpected restart.
7. Roll back, remove credentials and files, restore links/counters, and verify critical services and expected BGP sessions.

### Tools

| Interface | Tools |
|---|---|
| SNMP | `get_snmp_facts`, `snmpget`, `snmpwalk`, `snmpbulkwalk`; numeric OIDs |
| gNMI | `tests/gnmi/helper.py`, `gnmi_get`, `gnmi_set`, `gnmi_cli`, Python gRPC stubs |
| gNOI | `gnoi_request`, generated OpenConfig protobuf stubs |
| RESTCONF | HTTPS client with `application/yang-data+json`, status/headers, and `ietf-restconf:errors` |
| State | PTF, Redis CLI, FRR, platform APIs, `show`/config facts |
| Recovery | `create_checkpoint`, `rollback`, service and critical-process checks |

### Result classification

| Result | Meaning |
|---|---|
| Pass | Advertised behavior and value checks succeed |
| Fail | Behavior is wrong, inconsistent, unsafe, or unhealthy |
| Not supported | Image does not advertise or build the optional feature |
| Blocked | Required lab capability is missing |
| Implementation gap | Placeholder MIB or explicit `Unimplemented` RPC; not counted as functional coverage |

## Testbed requirements

Use two testbeds only. Both must present a **T1 or T2** topology: DUT plus PTF, BGP neighbors, and LLDP-capable links so interface, route, ARP, FDB, and event cases can run.

| Testbed | Description | What it covers | Additional configuration |
|---|---|---|---|
| Virtual | KVM/VS DUT with PTF and neighbor VMs in t1 or t2 | Protocol, discovery, auth, CRUD, subscribe, SNMP identity/lifecycle, gNMI/gNOI non-hardware paths, RESTCONF | Enable `snmp`, `gnmi`/`telemetry`, and `mgmt-framework`. SNMP community. gNMI/REST certificates and roles. NTP skip on KVM is acceptable. Discover gNMI container/port (`telemetry` 50051 or `gnmi` 50052). |
| Hardware | Physical DUT suitable for t1 or t2, with PTF/TGen and neighbors | Everything on Virtual, plus ASIC counters, PFC/queue, entity/sensor/PSU, optics, multi-ASIC T2 namespaces, and disruptive reboot/image/reset | Same feature/cert setup as Virtual. Enable port/queue counter polling. Lossless QoS and PFC-capable peer for queue/PFC cases. Platform APIs for fans/PSU/thermals/transceivers. T2 chassis for multi-ASIC. Console/PDU and an alternate image for reboot/install/factory-reset. Re-apply gNMI certs after reboot. |

Capability skips (`snmp`, `gnmi`/`telemetry`, or `mgmt-framework` not present) must name the missing feature. Mark reboot, image install, and factory reset so they stay out of the default PR gate on either testbed.

---

## SNMP

### Feature description

SONiC runs Net-SNMP `snmpd` as master agent and `sonic_ax_impl` as an AgentX subagent. The subagent reads Redis, FRR/system, and platform state, caches it, and refreshes periodically.

Implemented families include MIB-II, IF-MIB, ENTITY-MIB, ENTITY-SENSOR-MIB, IP-FORWARD-MIB, Q-BRIDGE-MIB, LLDP-MIB, and selected Cisco and Dell/Force10 objects. Coverage is selective: not every RFC object is implemented. Some objects are placeholders (empty string, `0`, or a fixed enum).

Existing `tests/snmp` cases cover system identity, basic interface fields, default/loopback routes, LLDP, FDB, queue/PFC presence, physical entities, PSU, CPU 5-second utilization, and memory. The cases below add protocol/security, lifecycle, value fidelity, and objects that are missing or only presence-checked.

### SNMP test cases

#### TC 1: Agent listen addresses

**Test Objective:** Verify snmpd answers only on configured IPv4/IPv6/VRF agent addresses.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Record current SNMP listen configuration with `show snmpagentaddress` and `sonic-db-cli CONFIG_DB keys 'SNMP_AGENT_ADDRESS_CONFIG*'`.
2. Add a management IPv4 listener with `sudo config snmpagentaddress add <mgmt_ipv4> -p 161` and, if the DUT has IPv6, `sudo config snmpagentaddress add <mgmt_ipv6> -p 161`.
3. Confirm `snmpd` is listening with `sudo ss -lunp | grep snmpd` and `docker exec snmp ss -lunp`.
4. From the PTF/localhost, query a valid listener: `snmpget -v2c -c <rocommunity> <mgmt_ipv4> sysName.0` and confirm the hostname.
5. Query an unconfigured DUT address/VRF (for example a front-panel IP not in SNMP_AGENT_ADDRESS_CONFIG) with the same community and confirm timeout/no response.
6. Remove the extra listener with `sudo config snmpagentaddress del <ip> -p 161` and confirm it no longer answers.

#### TC 2: SNMPv2c community

**Test Objective:** Verify valid SNMPv2c RO communities succeed and invalid or deleted communities are denied.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/conftest.py

**Test Steps:**

1. Show configured communities with `show snmpcommunity` and `sonic-db-cli CONFIG_DB keys 'SNMP_COMMUNITY|*'`.
2. Query with the valid RO community: `snmpget -v2c -c <rocommunity> <mgmt_ip> sysDescr.0` and confirm a non-empty description.
3. Query with a wrong community: `snmpget -v2c -c wrongcomm <mgmt_ip> sysDescr.0` and confirm timeout or authorization failure; `docker logs snmp` must not crash.
4. Add a temporary community with `sudo config snmp community add tmp_ro ro`, query successfully, then delete it with `sudo config snmp community del tmp_ro`.
5. Repeat the query with `tmp_ro` and confirm it is denied after deletion.

#### TC 3: SNMPv3

**Test Objective:** Verify SNMPv3 USM authentication and privacy, and reject wrong credentials.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. If the image has no SNMPv3 CLI (`config snmp user`), skip as not supported.
2. Create a user with `sudo config snmp user add testv3 -a SHA -A <auth> -x AES -X <priv> -r ro`.
3. Query with matching credentials: `snmpget -v3 -l authPriv -u testv3 -a SHA -A <auth> -x AES -X <priv> <mgmt_ip> sysName.0`.
4. Repeat with `-l authNoPriv` if configured, then with a wrong auth or privacy key and confirm failure.
5. Delete the user with `sudo config snmp user del testv3` and confirm subsequent queries fail.

#### TC 4: PDU operations

**Test Objective:** Verify GET, GETNEXT, GETBULK, walks, and error handling for missing or malformed PDUs.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET a known scalar: `snmpget -v2c -c <rocommunity> <mgmt_ip> .1.3.6.1.2.1.1.5.0`.
2. GETNEXT from `.1.3.6.1.2.1.1` and confirm lexicographic order of system objects.
3. GETBULK: `snmpbulkwalk -v2c -c <rocommunity> -Cr10 <mgmt_ip> .1.3.6.1.2.1.2.2`.
4. Walk a missing instance (`ifDescr.<unused_ifIndex>`) and confirm `noSuchInstance`/`noSuchObject`.
5. Send an oversized/malformed request (large GETBULK max-repetitions) and confirm the agent stays up: `docker exec snmp supervisorctl status`.

#### TC 5: Cache convergence

**Test Objective:** Verify SNMP values track CONFIG_DB/APPL_DB changes within the subagent refresh window.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Read hostname from CLI and SNMP: `show hostname` and `snmpget -v2c -c <rocommunity> <mgmt_ip> sysName.0`.
2. Change hostname with `sudo config hostname snmp-conv-test` (or CONFIG_DB `DEVICE_METADATA|localhost hostname`).
3. Poll `sysName.0` until it matches, within 60 seconds.
4. Shut down a front-panel port with `sudo config interface shutdown Ethernet0` and poll `ifAdminStatus` until it reports down.
5. Restore hostname and `sudo config interface startup Ethernet0`.

#### TC 6: Service restart

**Test Objective:** Verify SNMP master and subagent restart does not leave missing or duplicate MIB rows.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Walk `ifTable` and record ifIndex/ifDescr: `snmpwalk -v2c -c <rocommunity> <mgmt_ip> ifDescr`.
2. Restart the container with `sudo systemctl restart snmp` (or `docker restart snmp`) and wait until `docker exec snmp supervisorctl status` shows `snmpd` and `snmp-subagent` RUNNING.
3. Walk `ifDescr` again and confirm the same unique indexes and names.
4. Restart only the subagent (`docker exec snmp supervisorctl restart snmp-subagent`) and repeat the walk.
5. Confirm `docker exec snmp supervisorctl status` remains RUNNING.

#### TC 7: Multi-ASIC indexes

**Test Objective:** Verify interface, route, and neighbor SNMP rows from every frontend namespace have unique indexes.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_interfaces.py

**Test Steps:**

1. List ASICs with `show platform summary` and `ip netns list`.
2. For each namespace, collect ports with `sonic-db-cli -n asic0 APPL_DB keys 'PORT_TABLE:*'` (repeat per ASIC).
3. Walk `ifDescr`/`ifIndex` and confirm every frontend port alias appears exactly once.
4. Walk `ipNetToMediaPhysAddress` and `ipCidrRouteDest` and confirm no colliding indexes across namespaces.
5. Compare SNMP ifIndex uniqueness: no duplicate index values in the walk.

#### TC 8: LLDP local capabilities

**Test Objective:** Verify `lldpLocSysCapSupported` and `lldpLocSysCapEnabled` match local LLDP state.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_lldp.py

**Test Steps:**

1. Confirm LLDP is running: `systemctl is-active lldp` and `show lldp table`.
2. Read local chassis from Redis: `sonic-db-cli APPL_DB hgetall LLDP_LOC_CHASSIS`.
3. GET `.1.0.8802.1.1.2.1.3.5.0` (`lldpLocSysCapSupported`) and `.1.0.8802.1.1.2.1.3.6.0` (`lldpLocSysCapEnabled`).
4. Decode the BITS values and compare with APPL_DB / `docker exec lldp lldpcli show chassis`.
5. If the platform allows changing advertised capabilities, change them, wait for refresh, and confirm the OIDs update.

#### TC 9: ifNumber lifecycle

**Test Objective:** Verify `ifNumber` equals unique `ifTable` rows through VLAN/LAG add and delete.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_interfaces.py

**Test Steps:**

1. GET `ifNumber.0` (`.1.3.6.1.2.1.2.1.0`) and count unique `ifIndex` from `snmpwalk ... ifIndex`.
2. Create a VLAN with `sudo config vlan add 4094` and `sudo config vlan member add 4094 Ethernet0 -u` if needed.
3. Poll `ifNumber` and `ifDescr` until the VLAN interface appears.
4. Create a PortChannel with `sudo config portchannel add PortChannel4094` (use a free ID) and confirm a new SNMP row and incremented `ifNumber`.
5. Delete the VLAN and PortChannel (`sudo config vlan del 4094`, `sudo config portchannel del PortChannel4094`) and confirm `ifNumber` and rows return to baseline with no stale ifIndex.

#### TC 10: MIB-II 32-bit counters

**Test Objective:** Verify 32-bit interface counters track COUNTERS_DB deltas for generated traffic.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Enable port counters: `sudo counterpoll port enable` and `show counterpoll`.
2. Baseline SNMP `ifInOctets`/`ifOutOctets`/`ifInUcastPkts`/`ifInErrors` for a test port and Redis `sonic-db-cli COUNTERS_DB hget COUNTERS:<oid> SAI_PORT_STAT_IF_IN_OCTETS`.
3. Send a known unicast stream from PTF to the port, then broadcast/multicast and (if possible) error/drop traffic.
4. Re-read SNMP and COUNTERS_DB; assert deltas match in direction and type, including 32-bit wrap/mask.
5. Clear or restore counterpoll as required by the lab.

#### TC 11: IF-MIB HC counters

**Test Objective:** Verify 64-bit IF-MIB counters match COUNTERS_DB and agree with the low 32 bits of MIB-II counters.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Read `ifHCInOctets` (`.1.3.6.1.2.1.31.1.1.1.6`) and `ifInOctets` for the same ifIndex.
2. Generate enough traffic to increase both counters.
3. Compare 64-bit SNMP values with COUNTERS_DB `SAI_PORT_STAT_IF_IN_OCTETS` / `SAI_PORT_STAT_IF_OUT_OCTETS`.
4. Confirm `(ifHCInOctets & 0xffffffff) == ifInOctets` (and the outbound pair) after the same sample.
5. Repeat for unicast/multicast/broadcast HC packet counters.

#### TC 12: Counter object types

**Test Objective:** Verify port, LAG/RIF, and management-interface counter semantics.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_interfaces.py

**Test Steps:**

1. Identify a physical port, a PortChannel, a VLAN/RIF, and `eth0` from `show interfaces status` and `show interfaces counters`.
2. Walk `ifInOctets`/`ifOutOctets` for each type.
3. For PortChannel, sum member COUNTERS_DB values and compare with the LAG SNMP counters.
4. For the management interface, confirm documented zero or Linux-counter behavior.
5. Confirm physical ports track COUNTERS_DB directly.

#### TC 13: ifName, ifHighSpeed, and ifAlias

**Test Objective:** Verify IF-MIB name, speed, and alias track CONFIG_DB through description and LAG changes.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_interfaces.py

**Test Steps:**

1. From `show interfaces status`, pick Ethernet0 and record alias, speed, and description.
2. GET `ifName`, `ifHighSpeed` (`.1.3.6.1.2.1.31.1.1.1.15`), and `ifAlias` (`...1.18`) for that ifIndex.
3. Set description with `sudo config interface description Ethernet0 snmp-alias-test` and poll `ifAlias` until it matches.
4. Compare `ifHighSpeed` with `sonic-db-cli CONFIG_DB hget 'PORT|Ethernet0' speed` (Mbps). For speeds > 4 Gbps also check `ifSpeed` cap at 4294967295.
5. If a LAG exists, confirm LAG `ifHighSpeed` equals the sum of member speeds.

#### TC 14: Route table lifecycle

**Test Objective:** Verify `ipRouteNextHop`, `ipCidrRouteDest`, and `ipCidrRouteStatus` follow route add/change/delete.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_default_route.py

**Test Steps:**

1. Record FIB with `show ip route` and SNMP `snmpwalk ... .1.3.6.1.2.1.4.24.4`.
2. Add a static route: `sudo config route add prefix 192.0.2.0/24 nexthop <nh>`.
3. Walk `ipCidrRouteDest` and `ipCidrRouteStatus` until the prefix appears with status active(1).
4. GET `ipRouteNextHop` for the matching entry and compare with `show ip route 192.0.2.0/24`.
5. Change or delete the route (`sudo config route del prefix 192.0.2.0/24 nexthop <nh>`) and confirm the SNMP row disappears.

#### TC 15: ARP table lifecycle

**Test Objective:** Verify `ipNetToMediaPhysAddress` tracks neighbor add and age/delete.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Ping a directly connected neighbor to populate ARP: `ping -c 3 <neigh_ip>`.
2. Compare `show arp` / `ip neigh` with `snmpwalk ... .1.3.6.1.2.1.4.22.1.2`.
3. Confirm ifIndex and MAC encoding match APPL_DB `NEIGH_TABLE`.
4. Clear the neighbor (`sudo ip neigh del <neigh_ip> dev <if>` or wait for aging) and poll until the SNMP row is gone.
5. On multi-ASIC hardware, repeat for a neighbor in a non-default namespace.

#### TC 16: Q-BRIDGE FDB

**Test Objective:** Verify `dot1qTpFdbPort` maps VLAN+MAC to the correct ifIndex through learn, move, and age.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_fdb.py

**Test Steps:**

1. Create or use a VLAN with `show vlan brief` and send a tagged frame from PTF with a unique MAC.
2. Confirm learning with `show mac` and `sonic-db-cli ASIC_DB keys '*FDB_ENTRY*'`.
3. Walk `.1.3.6.1.2.1.17.7.1.2.2.1.2` and confirm VLAN+MAC index and port ifIndex.
4. Move the MAC to another VLAN member from PTF and confirm SNMP updates the port.
5. Stop traffic, wait for aging (`show mac` empty for that MAC), and confirm the SNMP row is removed.

#### TC 17: Sensor status

**Test Objective:** Verify `entPhySensorStatus` tracks present, unavailable, and faulted platform sensors.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_phy_entity.py

**Test Steps:**

1. Collect sensors with `show platform temperature` / `show environment` and `sonic-db-cli STATE_DB keys '*_INFO|*'`.
2. Walk ENTITY-SENSOR-MIB `.1.3.6.1.2.1.99.1.1.1` and map `entPhySensorStatus` (`.1.3.6.1.2.1.99.1.1.1.5`) to STATE_DB.
3. If the platform supports it, inject unavailable/fault (remove PSU, unplug transceiver, or platform API) and poll the status OID.
4. Restore the sensor and confirm status returns to ok.
5. Do not treat a related operational-status object as a substitute for this OID.

#### TC 18: Cisco FRU PSU status

**Test Objective:** Verify Cisco FRU PSU status matches STATE_DB for present/OK, absent, and failed.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_psu.py

**Test Steps:**

1. List PSUs with `sudo psuutil status` and `show platform psustatus`.
2. Walk `.1.3.6.1.4.1.9.9.117.1.1.2.1.2` and map values 2/7/8 to presence/status.
3. Compare with `sonic-db-cli STATE_DB hgetall 'PSU_INFO|PSU 1'`.
4. If hardware allows, remove or fail a PSU and poll until SNMP matches STATE_DB.
5. Reinsert/restore the PSU and confirm SNMP returns to OK.

#### TC 19: Cisco queue stats

**Test Objective:** Verify Cisco switch QoS queue counters match queue mapping and generated traffic.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_queue_counters.py

**Test Steps:**

1. Enable queue polling: `sudo counterpoll queue enable`.
2. Map queues with `show queue counters Ethernet0` and `sonic-db-cli COUNTERS_DB hgetall COUNTERS_QUEUE_NAME_MAP`.
3. Walk `.1.3.6.1.4.1.9.9.580.1.5.5.1.4` for the test ifIndex and record counter IDs 1..8.
4. Send queue-specific traffic (DSCP/TC mapping) from PTF/TGen.
5. Compare SNMP deltas with `SAI_QUEUE_STAT_*` in COUNTERS_DB for the matching queue OID.

#### TC 20: Cisco PFC per priority

**Test Objective:** Verify per-priority PFC request/indication counters track SAI PFC 0..7 independently.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_pfc_counters.py

**Test Steps:**

1. Enable PFC on the test port (`sudo config interface pfc asymmetric Ethernet0 off` / lossless profile as used in the lab) and `show pfc counters Ethernet0`.
2. Baseline SNMP `.1.3.6.1.4.1.9.9.813.1.2.1.2` (prioRequests) and `.1.3.6.1.4.1.9.9.813.1.2.1.3` (prioIndications) for priorities 0..7.
3. Generate PFC on a single priority from the peer/TGen.
4. Confirm only that priority’s TX/RX SNMP counters increase, matching `SAI_PORT_STAT_PFC_<n>_TX_PKTS` / `_RX_PKTS`.
5. Repeat for another priority and confirm independence.

#### TC 21: Cisco PFC aggregates

**Test Objective:** Verify aggregate PFC counters equal the sum of priorities (and LAG members).

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_pfc_counters.py

**Test Steps:**

1. Read per-priority SNMP counters and aggregate OIDs `.1.3.6.1.4.1.9.9.813.1.1.1.1` / `.1.3.6.1.4.1.9.9.813.1.1.1.2`.
2. Sum priorities 0..7 from COUNTERS_DB and from SNMP.
3. If the DUT is a LAG, sum members and compare with the LAG row.
4. If aggregate SNMP equals only priority 3, record a defect; otherwise require full-sum equality.
5. Generate multi-priority PFC and re-check the sums.

#### TC 22: Cisco BGP peer2 state

**Test Objective:** Verify `cbgpPeer2State` follows IPv4/IPv6 BGP session state.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. List peers with `show bgp summary` and `vtysh -c 'show bgp summary'`.
2. Walk `.1.3.6.1.4.1.9.9.187.1.2.5.1.3` and decode address type/length/octets indexes.
3. Map SNMP state to Idle/Connect/Established and compare with FRR.
4. Shut a neighbor (`sudo config bgp shutdown neighbor <peer>` or interface shutdown) and poll until SNMP leaves Established.
5. Restore BGP and confirm Established. On T2, document per-namespace limitation if only one table is exposed.

#### TC 23: Force10 1/5 minute CPU and memory

**Test Objective:** Verify Force10 1-minute and 5-minute CPU and memory utilization track host samples.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_cpu.py

**Test Steps:**

1. GET five-second CPU `.1.3.6.1.4.1.6027.3.10.1.2.9.1.2` as a baseline (already covered).
2. GET `.1.3.6.1.4.1.6027.3.10.1.2.9.1.3` (1 min), `.1.4` (5 min), and `.1.5` (memory).
3. Compare with `top -bn1` / `cat /proc/meminfo` / `psutil` samples; values must be 0..100.
4. Apply CPU load (`stress` or a tight loop) for more than one minute and confirm 1-minute CPU rises.
5. After load stops, confirm 1-minute and 5-minute windows decay toward idle.

#### TC 24: ENTITY hierarchy

**Test Objective:** Verify entPhysicalTable parent/class/name/serial relations for chassis, PSU, fan, thermal, and transceiver.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_phy_entity.py

**Test Steps:**

1. Collect platform inventory: `show platform syseeprom`, `show platform fan`, `show platform psustatus`, `show interfaces transceiver`.
2. Walk `entPhysicalTable` `.1.3.6.1.2.1.47.1.1.1.1`.
3. For each row, compare `entPhysicalContainedIn`, `entPhysicalClass`, `entPhysicalName`, `entPhysicalSerialNumber`, `entPhysicalModelName`, and `entPhysicalIsFRU` with STATE_DB `*_INFO` tables.
4. Confirm transceivers nest under the correct port and PSUs/fans under chassis/drawer.
5. Skip missing FRUs with an explicit platform reason.

Placeholder objects (`ifPhysAddress`, `ifLastChange`, `ifSpecific`, `ifLinkUpDownTrapEnable`, `ifPromiscuousMode`, `ifConnectorPresent`, `ifCounterDiscontinuityTime`, `entPhysicalVendorType`, `entPhysicalAlias`, `entPhysicalAssetID`) may have optional stub-contract checks. Do not treat stub values as feature coverage.

---

## gRPC (gNMI and gNOI)

### Feature description

`sonic-gnmi` exposes gNMI Capabilities, Get, Set, and Subscribe. Native DB paths read CONFIG_DB, APPL_DB, STATE_DB, and COUNTERS_DB. Writes target CONFIG_DB and selected APPL_DB data. YANG/Translib paths use SONiC or OpenConfig models. A SetRequest is one ordered transaction: delete, then replace, then update. CONFIG_DB writes are intended to roll back on failure and persist to `config_db.json`.

gNOI System, File, and OS are registered on supporting images. Ping and Traceroute return `Unimplemented`. FactoryReset, Healthz, Containerz, Debug, ORAS, SonicService/JWT, and gNSI are build-dependent and must be gated by runtime inventory.

Existing `tests/gnmi` and `tests/telemetry` cases cover Capabilities, certificate auth, CONFIG_DB incremental/full replace and subscribe, APPL_DB DASH VNET, COUNTERS_DB get/poll/sample, selected events, System Time, cold/warm reboot, OS Verify/Activate, and KillProcess (currently skipped by marks). The cases below fill remaining functional gaps.

Setup for gNMI/gNOI:

1. Confirm `gnmi` or `telemetry` container is running.
2. Install CA, server, and client certificates; map client CN to roles.
3. Enable native write and certificate client auth for write tests.
4. Synchronize DUT time on non-KVM platforms.
5. Checkpoint configuration and restore after the module.

### gNMI test cases

#### TC 1: Capabilities

**Test Objective:** Verify Capabilities returns gNMI version, encodings, and the installed model list.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi.py

**Test Steps:**

1. Discover the gNMI container/port (`docker ps | grep -E 'gnmi|telemetry'`).
2. Run `docker exec <gnmi> gnmi_cli -insecure -capabilities -address 127.0.0.1:<port>` (or the PTF `gnmi_cli` equivalent with client certs).
3. Confirm `JSON_IETF` is listed and `sonic-db` (and YANG models, if Translib is enabled) appear without duplicates.
4. Compare model names/versions with files in the telemetry/gnmi image YANG bundle.
5. Repeat with a `gnmi_noaccess` client CN and confirm Capabilities is denied (`tests/gnmi/test_gnmi.py` role path).

#### TC 2: Native Get

**Test Objective:** Verify Get of CONFIG_DB, APPL_DB, STATE_DB, and COUNTERS_DB at table, key, and field granularity.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. GET `/sonic-db:CONFIG_DB/localhost/DEVICE_METADATA/localhost` and compare with `sonic-db-cli CONFIG_DB hgetall 'DEVICE_METADATA|localhost'`.
2. GET `/sonic-db:APPL_DB/localhost/PORT_TABLE/Ethernet0/oper_status` and compare with `sonic-db-cli APPL_DB hget 'PORT_TABLE:Ethernet0' oper_status`.
3. GET `/sonic-db:STATE_DB/localhost` for a known table (for example `TRANSCEIVER_INFO` on hardware or `PORT_TABLE` state).
4. GET `/sonic-db:COUNTERS_DB/localhost/COUNTERS_PORT_NAME_MAP/Ethernet0` and a COUNTERS oid field `SAI_PORT_STAT_IF_IN_ERRORS`.
5. Issue a multi-path Get and confirm timestamps, prefixes, and JSON_IETF types.

#### TC 3: Virtual and OTHERS Get

**Test Objective:** Verify documented virtual/OTHERS telemetry paths match Linux or Redis sources.

**Testbed:** Any

**sonic-mgmt coverage:** tests/telemetry/test_telemetry.py

**Test Steps:**

1. GET documented `OTHERS` paths such as `platform/cpu`, `proc/stat`, and `proc/meminfo` using `gnmi_get` with target `OTHERS`.
2. Compare CPU/memory with `cat /proc/stat` and `cat /proc/meminfo`.
3. GET virtual COUNTERS paths used by `tests/telemetry/test_telemetry.py` (queue buffer, Ethernet0 counters).
4. GET an unsupported path and confirm a canonical gRPC error.
5. On hardware, confirm port/queue virtual paths return non-empty SAI stats.

#### TC 4: OpenConfig interface Get/Set

**Test Objective:** Verify OC-YANG interface config Get/Set maps to SONiC port configuration.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `/openconfig-interfaces:interfaces/interface[name=Ethernet0]/config` with target `OC-YANG`.
2. Compare `enabled`/`mtu` with `show interfaces status Ethernet0` and CONFIG_DB `PORT|Ethernet0`.
3. SET mtu via `gnmi_set -update /openconfig-interfaces:interfaces/interface[name=Ethernet0]/config/mtu:@mtu.json`.
4. Confirm `sonic-db-cli CONFIG_DB hget 'PORT|Ethernet0' mtu` and `show interfaces status` match.
5. Restore the original MTU.

#### TC 5: SONiC YANG Get/Set

**Test Objective:** Verify SONiC YANG Get/Set commits valid data and rejects schema-invalid payloads without partial state.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET a SONiC YANG path such as `/sonic-port:sonic-port/PORT/PORT_LIST[ifname=Ethernet0]`.
2. SET a valid field (description or mtu) and confirm CONFIG_DB.
3. SET an invalid payload (wrong type/range) and confirm a YANG/CVL error.
4. Re-GET and confirm the invalid SET left the previous value unchanged.
5. Restore the original leaf.

#### TC 6: Incremental Set

**Test Objective:** Verify delete, replace, update, and create-if-absent on CONFIG_DB leaves and containers.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. Update `PORT|Ethernet0 admin_status` to down via gNMI Set update, matching `test_gnmi_configdb_incremental_01`.
2. Confirm `sonic-db-cli CONFIG_DB hget 'PORT|Ethernet0' admin_status` is `down` and `show interfaces status Ethernet0` is down.
3. Set admin_status back to `up`.
4. Send an update to an invalid table path (`PORTABC`) and confirm failure (`test_gnmi_configdb_incremental_02`).
5. Delete a disposable leaf (temporary description) and confirm it is removed from CONFIG_DB.

#### TC 7: Set operation order

**Test Objective:** Verify one SetRequest applies delete, then replace, then update, including overlapping paths.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Build a SetRequest that deletes a disposable CONFIG_DB key, replaces a sibling, and updates a third leaf, including an overlapping path.
2. Apply it in one RPC.
3. Dump `sonic-cfggen -d --print-data` / `sonic-db-cli CONFIG_DB` and confirm final state reflects mandated order.
4. Confirm SetResponse lists each path with the corresponding op.
5. Restore baseline configuration.

#### TC 8: Set rollback

**Test Objective:** Verify a multi-operation Set with one invalid operation leaves CONFIG_DB unchanged.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Checkpoint running config: `sudo config save -y` is not required; record `sonic-db-cli CONFIG_DB hgetall 'DEVICE_METADATA|localhost'`.
2. Send a Set with a valid leaf update followed by an invalid path/value in the same request.
3. Confirm the RPC fails.
4. Re-read CONFIG_DB and `/etc/sonic/config_db.json` and confirm no partial change.
5. Confirm `show runningconfiguration all` matches the pre-test snapshot for the touched tables.

#### TC 9: Concurrent Set and Get

**Test Objective:** Verify concurrent Sets serialize and Gets do not return torn CONFIG_DB data.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Start two clients that Set `DEVICE_METADATA|localhost cloudtype` to different values.
2. In parallel, Get the same leaf repeatedly.
3. After both Sets complete, confirm CONFIG_DB contains exactly one of the values, never a mix of fields from both writes.
4. Confirm no gNMI process crash (`docker exec <gnmi> ps aux | grep telemetry`).
5. Restore `cloudtype`.

#### TC 10: CONFIG_DB persistence

**Test Objective:** Verify full CONFIG_DB replace persists across config reload.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. Perform full replace of CONFIG_DB as in `test_gnmi_configdb_full_replace_01` (admin_status down on a port).
2. Run `sudo config save -y`.
3. Reload with `sudo config reload -y` and wait for critical services.
4. Confirm the replaced CONFIG_DB values are present (`show interfaces status`, `sonic-db-cli CONFIG_DB`).
5. Restore admin_status with `sudo config interface startup Ethernet0` and `sudo config save -y`.

#### TC 11: Subscribe ONCE

**Test Objective:** Verify Subscribe ONCE returns one snapshot, sync_response, then terminates.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Subscribe ONCE to `/sonic-db:CONFIG_DB/localhost/DEVICE_METADATA/localhost`.
2. Confirm one update with `bgp_asn`/`hostname` and a `sync_response`.
3. Confirm the stream closes without further notifications.
4. Repeat for a table path and a leaf path.
5. Subscribe to multiple paths in one ONCE request and confirm all are present.

#### TC 12: Subscribe SAMPLE and TARGET_DEFINED

**Test Objective:** Verify SAMPLE interval/heartbeat and TARGET_DEFINED mode.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. Subscribe SAMPLE to DEVICE_METADATA with a one-second interval using the existing helper (`gnmi_subscribe_streaming_sample`).
2. Count `bgp_asn` samples and confirm cadence.
3. Enable `suppress_redundant` and heartbeat; confirm heartbeat is not a false change.
4. Subscribe TARGET_DEFINED to the same path and record the selected mode.
5. Unsubscribe cleanly.

#### TC 13: Subscribe ON_CHANGE

**Test Objective:** Verify ON_CHANGE initial sync and ordered create/update/delete notifications.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. Subscribe ON_CHANGE to `/sonic-db:CONFIG_DB/localhost/DEVICE_METADATA`.
2. Confirm initial snapshot contains key `localhost`.
3. Change `bgp_asn` with `sonic-db-cli CONFIG_DB hset 'DEVICE_METADATA|localhost' bgp_asn <n>` several times.
4. Confirm ordered updates without stale duplicates.
5. Delete and recreate a disposable field and confirm delete/update notifications. Restore `bgp_asn`.

#### TC 14: Subscribe POLL

**Test Objective:** Verify POLL initial sync, one snapshot per poll, and errors for invalid poll sequencing.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. Subscribe POLL to DEVICE_METADATA and COUNTERS_PORT_NAME_MAP as in existing tests.
2. Trigger poll three times and confirm one snapshot each (`bgp_asn` or `oid` count).
3. Send a poll before subscribe is established or a malformed poll and confirm a canonical error.
4. Compare polled COUNTERS values with `sonic-db-cli COUNTERS_DB`.
5. Close the stream.

#### TC 15: Subscribe lifecycle

**Test Objective:** Verify cancel, deadline, disconnect, server restart, and reconnect release resources and resync.

**Testbed:** Any

**sonic-mgmt coverage:** tests/telemetry/test_telemetry_cert_rotation.py

**Test Steps:**

1. Open an ON_CHANGE subscribe and cancel from the client; confirm the server session ends (`docker logs <gnmi>`).
2. Open a subscribe with a short deadline and confirm it terminates.
3. Kill the client process and confirm no leftover gnmi_cli/server leak (`docker exec <gnmi> ps aux`).
4. Restart the gNMI container (`sudo systemctl restart gnmi` or `telemetry`) and reconnect.
5. Confirm the new stream delivers current state.

#### TC 16: EVENTS subscribe

**Test Objective:** Verify SONiC EVENTS for BGP/link with filters, heartbeat, and cache options.

**Testbed:** Any

**sonic-mgmt coverage:** tests/telemetry/test_events.py

**Test Steps:**

1. Subscribe: `gnmi_cli -t EVENTS -streaming_type ON_CHANGE -q all[heartbeat=5][usecache=false]`.
2. Flap a BGP neighbor (`sudo config bgp shutdown neighbor <peer>`) or a link (`sudo config interface shutdown Ethernet0`).
3. Confirm the matching event (`sonic-events-bgp:bgp-state` or link event) is received.
4. Enable a filter (`-expected_event`) and confirm unrelated events are dropped.
5. Restore BGP/link. Optionally reconnect with cache enabled and confirm documented cache behavior.

#### TC 17: Dial-out telemetry

**Test Objective:** Verify dial-out client connects, retries, and recovers from collector outage.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Configure dial-out destination in CONFIG_DB (`TELEMETRY_CLIENT` / dialout destination as implemented on the image).
2. Restart the dial-out client and confirm a connection to the collector.
3. Stop the collector, wait for retry/backoff logs, then restart it.
4. Confirm telemetry resumes without unbounded queue growth (`docker logs` / client stats).
5. Disable dial-out configuration after the test.

#### TC 18: Authentication matrix

**Test Objective:** Verify mTLS, username/password, and JWT; reject expired, wrong CA/CN/SAN, and revoked certs.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi.py

**Test Steps:**

1. Connect with the valid client cert CN `test.client.gnmi.sonic` and confirm Capabilities succeeds.
2. Connect with an unmapped CN and confirm Unauthenticated (`test_gnmi_authorize_failed_with_invalid_cname`).
3. Connect with the revoked cert while CRL is served from PTF (`test_gnmi_authorize_failed_with_revoked_cert`).
4. If password/JWT metadata is enabled on the image, try valid and invalid username/password and expired JWT.
5. Confirm only configured mechanisms succeed.

#### TC 19: Authorization matrix

**Test Objective:** Verify certificate roles restrict Get/Set/Subscribe by target.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. Set CN role `gnmi_config_db_noaccess` with `sonic-db-cli CONFIG_DB hset 'GNMI_CLIENT_CERT|test.client.gnmi.sonic' 'role@' gnmi_config_db_noaccess`.
2. Attempt Get/Set/Subscribe of CONFIG_DB and confirm denial.
3. Set `gnmi_config_db_readonly` and confirm Get/Subscribe succeed and Set fails.
4. Set `gnmi_config_db_readwrite` and confirm Set of `DEVICE_METADATA|localhost cloudtype` succeeds.
5. Restore the default role with `add_gnmi_client_common_name` equivalent.

#### TC 20: Negative Get/Set

**Test Objective:** Verify unknown target/origin, wrong encoding, oversize payload, and unavailable DB return canonical errors.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. GET an unknown target (`NOT_A_DB`) and confirm gRPC error.
2. SET with a non-JSON payload to a JSON_IETF path and confirm error.
3. SET a very large payload and confirm bounded rejection, not a crash.
4. Stop Redis temporarily only if safe in lab, GET CONFIG_DB, then start Redis; otherwise GET a missing table key.
5. Confirm `docker exec <gnmi> supervisorctl status` remains RUNNING.

#### TC 21: Multi-ASIC gNMI

**Test Objective:** Verify Get/Set/Subscribe of per-namespace ports and counters on T2.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Identify namespaces with `show platform summary` and `sonic-cfggen -n asic0 -d --print-data`.
2. GET CONFIG_DB PORT for an interface owned by asic0 and asic1.
3. SET admin_status on one namespace-owned port and confirm only that ASIC’s APPL_DB/CLI changes.
4. Subscribe COUNTERS for each namespace’s port and confirm oid maps do not collide.
5. Restore admin_status.

#### TC 22: Bulk Get/Set

**Test Objective:** Verify a bulk Get and a bulk Set complete atomically within configured limits.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET 20+ CONFIG_DB PORT admin_status paths in one GetRequest.
2. Confirm every path is in the response.
3. SET several disposable descriptions in one SetRequest.
4. Confirm all descriptions are in CONFIG_DB or none are if the request failed.
5. Clear the test descriptions with `sudo config interface description EthernetX ""`.

### gNOI test cases

#### TC 1: System.Time

**Test Objective:** Verify System.Time returns DUT clock in nanoseconds and increases.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnoi_system.py

**Test Steps:**

1. Record DUT time: `date +%s%N` (or `date +%s`).
2. Call gNOI System.Time via `gnoi_client` / `gnoi_request(..., 'System', 'Time', '')`.
3. Confirm the JSON `time` field is within 60 seconds of DUT clock.
4. Call Time twice more and confirm strictly increasing values.
5. Repeat using the Python stub path in `tests/gnmi/test_gnoi_system_grpc.py`.

#### TC 2: Reboot request validation

**Test Objective:** Verify invalid reboot methods and delayed reboot are rejected without rebooting.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnoi_system_reboot.py

**Test Steps:**

1. Record uptime: `uptime -s` / `cat /proc/uptime`.
2. Send Reboot with method UNKNOWN (0) and POWERUP (7); expect InvalidArgument.
3. Send Reboot with `delay > 0`; expect InvalidArgument.
4. Confirm uptime is unchanged and `show reboot-cause` did not record a new gNOI reboot.
5. Do not send COLD/WARM in this case (covered by TC 3).

#### TC 3: RebootStatus lifecycle

**Test Objective:** Verify RebootStatus before, during, and after an accepted COLD reboot.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/gnmi/test_gnoi_system_reboot.py

**Test Steps:**

1. Call RebootStatus while idle and record `active`/`count`.
2. Send COLD Reboot with message `gnoi test reboot` (`gnoi_request` System Reboot).
3. Immediately call RebootStatus and confirm `active=true`, reason, method=COLD, count incremented.
4. Wait for the DUT (`wait_for_startup`) and `show system status` / critical processes.
5. Re-apply gNMI certs (`apply_cert_config` equivalent) and confirm uptime reset.

#### TC 4: CancelReboot

**Test Objective:** Verify CancelReboot idle and pending behavior.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Call CancelReboot while idle and record accepted or documented rejection.
2. If delayed reboot is unsupported, confirm CancelReboot does not change uptime.
3. Call RebootStatus and confirm it is consistent with the cancel result.
4. Do not leave a pending reboot after the test.
5. Capture gNOI and syslog (`show logging | grep -i reboot`).

#### TC 5: KillProcess

**Test Objective:** Verify KillProcess stops and restarts allowlisted services and rejects invalid parameters.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnoi_killprocess.py

**Test Steps:**

1. Confirm `snmp` is running: `docker ps | grep snmp`.
2. Send KillProcess `{"name":"snmp","signal":1}` and confirm the container/service stops.
3. Send KillProcess `{"name":"snmp","restart":true,"signal":1}` and confirm it returns.
4. Send invalid name `gnmi` / empty name / `signal:2` and confirm the documented errors from `test_gnoi_killprocess.py`.
5. Wait for critical processes (`wait_critical_processes`). Re-enable this test if still globally skipped.

#### TC 6: Ping and Traceroute unimplemented

**Test Objective:** Verify Ping and Traceroute return Unimplemented.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Call System.Ping with destination `127.0.0.1`.
2. Confirm gRPC status Unimplemented (not timeout or success).
3. Call System.Traceroute to a neighbor IP.
4. Confirm Unimplemented.
5. Confirm the gNOI process is still running.

#### TC 7: SwitchControlProcessor

**Test Objective:** Verify SwitchControlProcessor performs a real control-plane switch or is documented as a no-op.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Record active CP with `show platform summary` / chassis commands.
2. Call SwitchControlProcessor with the standby CP path if present.
3. If the RPC returns empty success, verify whether CP actually switched; if not, classify as implementation gap.
4. If a switch occurs, wait for services and re-apply gNMI certs.
5. Skip on single-CP platforms with an explicit reason.

#### TC 8: SetPackage

**Test Objective:** Verify SetPackage streaming, hash, and failure cleanup.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Stream a small allowlisted package via gNOI System.SetPackage.
2. Confirm the file appears under the documented target directory (`ls -l`).
3. Interrupt a second stream mid-upload and confirm leftover data is removed.
4. Send overlapping concurrent SetPackage calls and confirm rejection or serialization.
5. Remove test files.

#### TC 9: File Stat and Get

**Test Objective:** Verify File.Stat and File.Get return metadata and content for an allowlisted file.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Create `/tmp/gnoi_stat_test` with known content: `echo sonic-gnoi-file > /tmp/gnoi_stat_test`.
2. Call File.Stat on that path and compare size/mtime with `stat /tmp/gnoi_stat_test`.
3. Call File.Get and reassemble chunks; compare with `sha256sum /tmp/gnoi_stat_test`.
4. Call Stat/Get on a missing file and confirm NotFound.
5. Delete `/tmp/gnoi_stat_test`.

#### TC 10: File Put and Remove

**Test Objective:** Verify File.Put, overwrite, Get, and Remove lifecycle.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Put a file to `/tmp/gnoi_put_test` with known bytes.
2. Get it back and compare checksums.
3. Put again with different content (overwrite) and confirm Get returns the new bytes.
4. Remove the file and confirm `ls /tmp/gnoi_put_test` fails and Get returns NotFound.
5. Interrupt a Put stream and confirm no partial file remains.

#### TC 11: File access control

**Test Objective:** Verify path traversal, forbidden paths, and TransferToRemote stay in the allowlist.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Attempt Stat/Get/Remove on `/etc/shadow` and `../../etc/passwd`; confirm PermissionDenied.
2. Attempt Put to a symlink outside `/tmp`, `/var/tmp`, `/host`.
3. Call TransferToRemote with a disallowed URL or failed TLS and confirm error.
4. Confirm no unexpected files with `find / -name gnoi_*` limited to allowlisted roots.
5. Confirm gNOI remains running.

#### TC 12: OS Verify and Activate

**Test Objective:** Verify OS.Verify matches the running image and Activate handles current vs missing versions.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnoi_os.py

**Test Steps:**

1. Record current image: `show version` / `sonic-installer list`.
2. Call OS.Verify and compare `version` with `image_facts` current image.
3. Call Activate with the current version and expect ActivateOk.
4. Call Activate with `invalid-image-name` and expect ActivateError `Image does not exist`.
5. Confirm `sonic-installer list` is unchanged.

#### TC 13: OS Install

**Test Objective:** Verify OS.Install streams an image, reports progress, and rejects concurrent/partial transfers.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Ensure free disk: `df -h /host /tmp`.
2. Start OS.Install TransferRequest with a unique version string.
3. Stream content and confirm TransferProgress bytes increase.
4. Send TransferEnd and confirm Validated or the documented success response.
5. Start a second concurrent Install and confirm INSTALL_IN_PROGRESS / Aborted. Interrupt a transfer and confirm the partial file is removed.

#### TC 14: OS Install path safety

**Test Objective:** Verify Install rejects traversal and malformed stream order.

**Testbed:** Virtual

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Send TransferContent before TransferRequest and confirm InvalidArgument.
2. Request a version path containing `../` and confirm no write outside the image directory (`ls` / `find`).
3. Send a second TransferRequest in the middle of content and confirm error.
4. Confirm Install mutex released by running a subsequent well-formed negative request.
5. Remove any leftover files under the image directory.

#### TC 15: Optional gNOI and gNSI services

**Test Objective:** Inventory FactoryReset, Healthz, Containerz, Debug, ORAS, SonicService/JWT, and gNSI; test advertised RPCs only.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. List gRPC services (server reflection or probe RPCs) and record which are registered.
2. For Healthz, call Get if present; expect Unimplemented for List/Check if documented.
3. For FactoryReset, run only on a reserved DUT with PDU; otherwise skip with reason.
4. Smoke-test SonicService ShowTechsupport or Debug whitelist if advertised.
5. Do not fail the suite for unadvertised services.

#### TC 16: gNOI RBAC

**Test Objective:** Verify readonly vs readwrite roles on System, File, and OS.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Set client CN role `gnoi_readonly` via `GNMI_CLIENT_CERT`.
2. Call System.Time (read) and expect success; call KillProcess/Reboot/File.Put/OS.Activate and expect denial.
3. Set role `gnoi_readwrite` and confirm a non-destructive write (File.Put under `/tmp`) succeeds.
4. Restore the default role.
5. Confirm denied calls made no filesystem or service change.

Factory reset, powerdown/halt, and image install/activate must not run in a normal PR gate.

---

## RESTCONF

### Feature description

Management Framework RESTCONF is served by `mgmt-framework` on `/restconf` over HTTPS. It is not the `restapi` container `/v1` VNET/VXLAN API. Discovery includes `/.well-known/host-meta`, YANG library, RESTCONF capabilities, operations list, YANG download, and Swagger UI. Authentication modes are Basic/user, client certificate, optional JWT, or none for debug. Writes require an admin-capable user. Errors use `ietf-restconf:errors`. Notifications are not supported.

`tests/restapi` covers the separate `restapi` container (`/v1/...`) and is not this suite. Add `tests/restconf` with fixtures that:

1. Require `mgmt-framework` and discover the HTTPS port.
2. Create certificates and temporary users/roles where supported.
3. Use RESTCONF media types and a verified session.
4. Discover a safe writable model (OpenConfig interfaces, SONiC port/VLAN, or ACL).
5. Checkpoint and roll back all configuration.

Discover JWT only when `/authenticate` and JWT mode are present. Basic auth requires DUT SSH, because the open-source server authenticates through `127.0.0.1:22`.

### RESTCONF test cases

#### TC 1: TLS

**Test Objective:** Verify RESTCONF is HTTPS-only and validates the TLS chain.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `curl -sk -u admin:<password> -H 'Accept: application/yang-data+json' https://<mgmt_ip>/restconf/data/ietf-yang-library:modules-state` with `-k` and with a trusted CA.
2. Connect with the wrong CA or expired client/server cert and confirm failure.
3. Attempt plain HTTP to port 443/80 (`curl http://<mgmt_ip>/restconf`) and confirm no silent RESTCONF success.
4. Confirm TLS 1.2+ with `openssl s_client -connect <mgmt_ip>:443`.
5. Record `REST_SERVER|default` from `sonic-db-cli CONFIG_DB hgetall 'REST_SERVER|default'`.

#### TC 2: Discovery

**Test Objective:** Verify RESTCONF discovery documents, YANG library, capabilities, and operations list.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `curl -sk -u admin:<password> https://<mgmt_ip>/.well-known/host-meta` and confirm XRD restconf=`/restconf`.
2. GET `curl -sk -u admin:<password> -H 'Accept: application/yang-data+json' https://<mgmt_ip>/restconf/yang-library-version`.
3. GET `curl -sk -u admin:<password> -H 'Accept: application/yang-data+json' https://<mgmt_ip>/restconf/data/ietf-yang-library:modules-state`.
4. GET `curl -sk -u admin:<password> -H 'Accept: application/yang-data+json' https://<mgmt_ip>/restconf/data/ietf-restconf-monitoring:restconf-state/capabilities`.
5. GET `curl -sk -u admin:<password> -H 'Accept: application/yang-data+json' https://<mgmt_ip>/restconf/operations` and optionally download a YANG file from `/models/yang/`.

#### TC 3: Swagger UI

**Test Objective:** Verify `/ui` loads and advertised OpenAPI paths match discoverable models.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `curl -sk -u admin:<password> -o /dev/null -w '%{http_code}' https://<mgmt_ip>/ui` and expect 200.
2. Open an advertised OpenAPI definition from the UI page.
3. Pick one path (for example openconfig-interfaces) and GET the corresponding `/restconf/data/...` URL.
4. Confirm the UI path and RESTCONF path refer to the same model.
5. Skip if `/ui` is not packaged, with an explicit image reason.

#### TC 4: GET data

**Test Objective:** Verify GET of containers, lists, leaves, config, and state matches CLI/Redis.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `curl -sk -u admin:<password> -H 'Accept: application/yang-data+json' https://<mgmt_ip>/restconf/data/openconfig-interfaces:interfaces`.
2. GET a keyed interface: `/restconf/data/openconfig-interfaces:interfaces/interface=Ethernet0`.
3. GET `/restconf/data/sonic-port:sonic-port/PORT/PORT_LIST=Ethernet0/admin_status`.
4. Compare with `show interfaces status Ethernet0` and `sonic-db-cli CONFIG_DB hget 'PORT|Ethernet0' admin_status`.
5. GET a state leaf (oper-status) and compare with APPL_DB `oper_status`.

#### TC 5: HEAD

**Test Objective:** Verify HEAD returns GET status/headers with an empty body.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. HEAD `curl -sk -u admin:<password> -I -H 'Accept: application/yang-data+json' https://<mgmt_ip>/restconf/data/openconfig-interfaces:interfaces/interface=Ethernet0`.
2. GET the same URL and compare status and Content-Type.
3. Confirm HEAD body is empty.
4. HEAD a missing resource and confirm 404.
5. Confirm Content-Length is present on HEAD of an existing resource.

#### TC 6: OPTIONS

**Test Objective:** Verify OPTIONS Allow/Accept-Patch match the YANG node type.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. `curl -sk -u admin:<password> -X OPTIONS -D - https://<mgmt_ip>/restconf/data/openconfig-interfaces:interfaces`.
2. Confirm `Allow` includes GET and write methods as applicable.
3. OPTIONS a config leaf that supports PATCH and confirm `Accept-Patch: application/yang-data+json`.
4. OPTIONS `/restconf/operations` and a state-only node; confirm write methods are absent where required.
5. OPTIONS an unknown path and confirm 404 or documented Allow.

#### TC 7: POST create

**Test Objective:** Verify POST creates a disposable object and duplicate POST conflicts.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. POST a VLAN (or loopback) JSON to the appropriate list URL, for example `/restconf/data/sonic-vlan:sonic-vlan`.
2. Expect 201 and optional Location.
3. Confirm `show vlan brief` and CONFIG_DB `VLAN|Vlan4094`.
4. POST the same object again and expect 409 with `ietf-restconf:errors`.
5. DELETE the VLAN (`sudo config vlan del 4094` or REST DELETE).

#### TC 8: PUT replace

**Test Objective:** Verify PUT create/replace and defaulting of omitted non-default leaves.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PUT admin_status/mtu for Ethernet0 with a full config object.
2. GET the resource and compare with CONFIG_DB.
3. PUT again omitting a non-default leaf and confirm documented default/replace behavior (no unintended sibling deletion).
4. Confirm `show interfaces status Ethernet0`.
5. Restore original MTU/admin_status with `sudo config interface ...`.

#### TC 9: PATCH merge

**Test Objective:** Verify PATCH changes only supplied leaves and is idempotent.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH `curl -sk -u admin:<password> -X PATCH -H 'Content-Type: application/yang-data+json' -d '{"sonic-port:admin_status":"down"}' https://<mgmt_ip>/restconf/data/sonic-port:sonic-port/PORT/PORT_LIST=Ethernet0/admin_status`.
2. Confirm `show interfaces status Ethernet0` is down.
3. PATCH the same payload again and confirm still down (idempotent).
4. PATCH admin_status to `up`.
5. Confirm BGP/neighbors recover if the port was in use, or use a spare port.

#### TC 10: YANG Patch

**Test Objective:** Verify YANG Patch multi-edit success and atomic failure.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Send `PATCH` with `Content-Type: application/yang-patch+json` containing two valid edits (for example two descriptions).
2. Confirm both CONFIG_DB leaves changed and yang-patch-status `ok`.
3. Send a patch where the second edit is invalid.
4. Confirm per-edit status and that the first edit was rolled back if atomicity is advertised.
5. Restore descriptions.

#### TC 11: DELETE

**Test Objective:** Verify DELETE of leaf/list/container and repeated DELETE.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Create Vlan4094 via POST or `sudo config vlan add 4094`.
2. DELETE `curl -sk -u admin:<password> -X DELETE https://<mgmt_ip>/restconf/data/sonic-vlan:sonic-vlan/VLAN/VLAN_LIST=Vlan4094`.
3. Confirm `show vlan brief` has no 4094.
4. DELETE again and expect 404 / data-missing.
5. If capabilities include `deleteEmptyEntry`, DELETE with `?deleteEmptyEntry=true` on a parent and confirm empty-parent cleanup.

#### TC 12: RPC operations

**Test Objective:** Verify POST to `/restconf/operations` for a safe RPC and structured errors.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `/restconf/operations` and pick a safe RPC (for example `sonic-show-techsupport` if listed).
2. POST valid input JSON to `/restconf/operations/<rpc>`.
3. Confirm 200 and any output file/log (`ls /var/dump` or documented path).
4. POST malformed JSON and an unknown RPC name; expect 400/404 and `ietf-restconf:errors`.
5. Skip if no safe RPC is advertised.

#### TC 13: Query parameters

**Test Objective:** Verify `depth`, `content`, and `fields` filter GET subtrees.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET interfaces with `?depth=1` and without `depth`; confirm the shallow response is smaller.
2. GET `?content=config` and `?content=nonconfig` (or `all`) and compare config vs state leaves.
3. GET `?fields=interface/name` (or the advertised fields syntax) and confirm only requested nodes.
4. Confirm capabilities include the corresponding URNs from TC 2.
5. If the image ignores query parameters, record not-supported rather than fail.

#### TC 14: Invalid query parameters

**Test Objective:** Verify unknown or illegal query parameters return 400/405.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET with `?depth=0` or `?depth=abc`.
2. GET with `?fields=...&depth=1` if the combination is illegal.
3. GET with `?notAParam=1`.
4. DELETE with `?depth=1`.
5. Confirm 400/405 and `ietf-restconf:errors`; confirm no config change.

#### TC 15: Basic authentication

**Test Objective:** Verify HTTP Basic succeeds only with a valid admin-capable user.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET with `-u admin:<password>` and expect 200.
2. GET without `-u` and expect 401.
3. GET with `-u admin:wrong` and expect 401.
4. If a non-admin user exists, GET may succeed and PATCH must return 403.
5. Confirm `sonic-db-cli CONFIG_DB hget 'REST_SERVER|default' client_auth` includes password/user.

#### TC 16: JWT authentication

**Test Objective:** Verify JWT issue/use/expiry if `/authenticate` exists.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. POST `https://<mgmt_ip>/authenticate` with username/password JSON.
2. If 404, skip as not supported.
3. GET RESTCONF with `Authorization: Bearer <token>` and expect 200.
4. GET with a tampered/expired token and expect 401.
5. Do not require JWT on images that only support Basic/cert.

#### TC 17: Client certificate authentication

**Test Objective:** Verify cert-mode RESTCONF accepts only mapped trusted certificates.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Set `sonic-db-cli CONFIG_DB hset 'REST_SERVER|default' client_auth cert` and install `ca_crt`/`server_crt`.
2. Restart with `sudo systemctl restart mgmt-framework`.
3. GET with `--cert client.crt --key client.key` and expect 200.
4. GET with an unknown/revoked client cert and expect failure.
5. Restore `client_auth` to `user` and restart mgmt-framework.

#### TC 18: Authorization

**Test Objective:** Verify readonly/noaccess cannot write; admin writes succeed.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH admin_status as admin and confirm CONFIG_DB changes.
2. PATCH as a non-admin user and expect 403 with no CONFIG_DB change.
3. GET as readonly should succeed if that role exists.
4. POST a VLAN as noaccess/readonly and expect 403.
5. Restore Ethernet0 admin_status with `sudo config interface startup Ethernet0`.

#### TC 19: Media type and schema errors

**Test Objective:** Verify wrong Content-Type and invalid JSON/YANG data return 400/415 without commit.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH with `Content-Type: text/plain` and expect 415.
2. PATCH malformed JSON and expect 400.
3. PATCH `admin_status` to `not-a-status` and expect 400 invalid-value.
4. Confirm CONFIG_DB admin_status unchanged.
5. PATCH with wrong module prefix and expect 400.

#### TC 20: Resource errors

**Test Objective:** Verify 404, 409, and 405 mappings and error-tag values.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET a missing VLAN `/VLAN_LIST=Vlan4093` and expect 404.
2. POST a duplicate VLAN and expect 409.
3. PUT/PATCH a method not allowed on a state-only node and expect 405.
4. Parse `ietf-restconf:errors` error-type and error-tag.
5. Confirm no unexpected VLAN remains in `show vlan brief`.

#### TC 21: Accept-Version

**Test Objective:** Verify Accept-Version handling against the YANG bundle version.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET modules-state `module-set-id` / bundle version.
2. GET a data resource without `Accept-Version` and expect success.
3. GET with `Accept-Version: 1.0.0` (or current) and with a malformed header.
4. GET with an unsupported future version and expect 400 operation-not-supported.
5. Confirm OpenAPI-only paths ignore Accept-Version if that is documented.

#### TC 22: Conditional headers

**Test Objective:** Verify ETag/If-Match behavior on the running image.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET a resource and record ETag/Last-Modified if present.
2. GET/PATCH with `If-Match: "*"` and `If-Modified-Since` in the past.
3. Confirm the image either honors them or ignores them consistently (HLD: ignored).
4. Do not fail if headers are absent.
5. Confirm the PATCH still applies or is ignored per observed rules.

#### TC 23: Cross-NBI consistency

**Test Objective:** Verify RESTCONF writes are visible in CLI/Redis/gNMI and the reverse.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH Ethernet0 description via RESTCONF.
2. Confirm `show interfaces status Ethernet0` and `sonic-db-cli CONFIG_DB hget 'PORT|Ethernet0' description`.
3. If gNMI is enabled, GET the same leaf via `/sonic-db:CONFIG_DB/.../description`.
4. Set description via `sudo config interface description Ethernet0 from-cli` and GET via RESTCONF.
5. Clear the description.

#### TC 24: Persistence

**Test Objective:** Verify RESTCONF config survives mgmt-framework restart and config reload.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH a disposable description and `sudo config save -y`.
2. Restart `sudo systemctl restart mgmt-framework` and GET the description.
3. Run `sudo config reload -y`, wait for services, and GET again.
4. Confirm state-only data was not persisted.
5. Clear the description.

#### TC 25: Concurrency

**Test Objective:** Verify parallel GETs succeed and overlapping writes do not tear state.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Run 10 parallel GETs of `/openconfig-interfaces:interfaces`.
2. Run two parallel PATCHes of the same leaf to different values.
3. Read CONFIG_DB and confirm a single final value.
4. If 409 in-use is returned, confirm it is deterministic.
5. Restore the leaf.

#### TC 26: Multi-ASIC RESTCONF

**Test Objective:** Verify per-namespace interface objects do not collide on T2.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. List interfaces per ASIC with `show interfaces status` and namespace mapping.
2. GET Ethernet interfaces owned by different ASICs.
3. PATCH mtu on one ASIC-owned port.
4. Confirm only that port’s CONFIG_DB/namespace changed (`sonic-db-cli -n asicN`).
5. Restore mtu.

#### TC 27: Notifications unsupported

**Test Objective:** Verify RESTCONF notification streams are not advertised and are rejected.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Read capabilities and confirm no notification URN.
2. GET/POST a notifications URL if one exists (`/restconf/streams`).
3. Expect 404/405 or missing capability.
4. Document that monitoring must use gNMI Subscribe.
5. Confirm the REST server stays healthy.

#### TC 28: Server restart during requests

**Test Objective:** Verify mgmt-framework restart and malformed-request soak do not panic.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Start a loop of GET `/restconf/data/openconfig-interfaces:interfaces`.
2. Restart `sudo systemctl restart mgmt-framework` mid-loop.
3. Confirm GETs recover to 200.
4. Send 50 malformed PATCH bodies; expect 400s.
5. Confirm `docker ps | grep mgmt-framework` is Up and logs have no panic.

Protocol, discovery, security, CRUD, persistence, and concurrency run on Virtual and Hardware when `mgmt-framework` is enabled. Platform YANG (optics, PSU, fan, ASIC counters) is Hardware-only.

---

## Entry, exit, and acceptance criteria

### Entry criteria

* DUT is reachable and critical services are healthy.
* Required feature containers are present and enabled.
* PTF/localhost can reach SNMP, gNMI, and RESTCONF ports.
* Credentials, certificates, SNMP communities, NTP, and optional collectors/image servers are available.
* The selected testbed profile has the needed neighbors, traffic, or platform APIs.
* Checkpoint and recovery have been validated.

### Exit criteria

* Every selected case has a result and captured request/response evidence.
* Configuration, links, services, credentials, and temporary files are restored.
* Critical processes and expected routing sessions are healthy.

### Acceptance criteria

* Core positive read/CRUD/subscribe flows pass.
* No authentication or authorization bypass.
* No partial CONFIG_DB transaction after an expected atomic failure.
* No panic, crash loop, persistent outage, or unrecoverable configuration.
* Dynamic state and counters converge and match their source of truth.
* Placeholder MIB values and `Unimplemented` RPCs are classified as implementation gaps.

## Implementation order

1. SNMP protocol, community/v3, `ifNumber`, LLDP capabilities, `ifName`/`ifAlias`, and route/ARP/FDB lifecycle on Virtual.
2. gNMI model/target matrix, Set transaction order/rollback, ONCE/TARGET_DEFINED, and security.
3. gNOI File and negative System/OS cases; isolate reboot/image tests; re-enable KillProcess if still skipped.
4. `tests/restconf` fixtures, discovery, method semantics, authz, errors, and safe CRUD on Virtual.
5. Hardware counter, PFC, entity, and T2 multi-ASIC cases.
