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

1. Read `/etc/sonic/snmp.yml` and inventory every `snmp_rocommunity`, `snmp_rocommunities`, `snmp_rwcommunity`, and `snmp_rwcommunities` value used by the automated fixture.
2. Compare those values with `redis-cli -n 4 keys 'SNMP_COMMUNITY*'` / `show snmpcommunity`; add each missing RO or RW community with `sudo config snmp community add <community> ro|rw`.
3. If `snmp_location` is defined, compare it with `redis-cli -n 4 keys 'SNMP|LOCATION*'` and provision it with `sudo config snmp location add <location>` when absent.
4. For every configured RO community, run `snmpget -v2c -c <community> <mgmt_ip> sysDescr.0`; expect a successful response containing a non-empty description.
5. Query with `wrongcomm`; expect timeout or authorization failure. Add and then delete `tmp_ro` with `sudo config snmp community add tmp_ro ro` and `sudo config snmp community del tmp_ro`.
6. Repeat the query with `tmp_ro`; expect no response after deletion and confirm the original inventory communities remain in CONFIG_DB.

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

#### TC 7: LLDP local and remote tables

**Test Objective:** Verify LLDP local chassis, port, management-address, capability, and remote-neighbor MIB data against LLDP and topology state.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_lldp.py

**Test Steps:**

1. Confirm LLDP is active with `systemctl is-active lldp`; collect minigraph neighbors from every frontend ASIC and `docker exec lldp lldpcli show neighbors -f keyvalue`.
2. Walk `lldpLocalSystemData` (`.1.0.8802.1.1.2.1.3`) and require non-empty `lldpLocChassisIdSubtype`, chassis ID, system name, and system description without `No Such Object`.
3. For every SNMP interface named `Ethernet*` or `eth*`, require `lldpLocPortIdSubtype`, `lldpLocPortId`, and `lldpLocPortDesc`; on a non-modular DUT also require all local management-address table fields.
4. GET `lldpLocSysCapSupported` and `lldpLocSysCapEnabled`, decode their BITS values, and compare them with `lldpcli show chassis` / `LLDP_LOC_CHASSIS` in APPL_DB.
5. Walk `lldpRemTable`; require chassis, port, system, description, and supported/enabled capability fields for at least 80% of non-server minigraph neighbors.
6. Walk `lldpRemManAddrTable`; its populated interface count must equal the `lldpctl` neighbors that advertise a management IP, excluding `eth0` and internal backplane links.

#### TC 8: ifNumber lifecycle

**Test Objective:** Verify `ifNumber` equals unique `ifTable` rows through VLAN/LAG add and delete.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `ifNumber.0` (`.1.3.6.1.2.1.2.1.0`) and count unique `ifIndex` from `snmpwalk ... ifIndex`.
2. Create a VLAN with `sudo config vlan add 4094` and `sudo config vlan member add 4094 Ethernet0 -u` if needed.
3. Poll `ifNumber` and `ifDescr` until the VLAN interface appears.
4. Create a PortChannel with `sudo config portchannel add PortChannel4094` (use a free ID) and confirm a new SNMP row and incremented `ifNumber`.
5. Delete the VLAN and PortChannel (`sudo config vlan del 4094`, `sudo config portchannel del PortChannel4094`) and confirm `ifNumber` and rows return to baseline with no stale ifIndex.

#### TC 9: MIB-II and IF-MIB interface counters

**Test Objective:** Verify 32-bit MIB-II and 64-bit IF-MIB counters track COUNTERS_DB deltas, preserve width semantics, and agree on their low 32 bits.

**Testbed:** Hardware

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Enable port polling with `sudo counterpoll port enable`; select a test interface and map it to its COUNTERS_DB OID through `COUNTERS_PORT_NAME_MAP`.
2. In one sampling window, read `ifInOctets`, `ifOutOctets`, unicast/multicast/broadcast packet counters, and their `ifHC*` counterparts for the same ifIndex; read the matching `SAI_PORT_STAT_*` values from COUNTERS_DB.
3. Send known unicast, multicast, and broadcast streams from PTF/TGen and, where supported, error/drop traffic; wait for the SNMP cache and COUNTERS_DB to update.
4. Assert 32-bit and 64-bit SNMP deltas match COUNTERS_DB in direction and packet class, and all MIB objects use the expected Counter32 or Counter64 type.
5. For each octet/packet pair sampled together, assert `(ifHCValue & 0xffffffff) == ifValue`; run enough traffic or seed a supported test counter to exercise 32-bit wrap without a 64-bit reset.
6. Stop traffic and restore the original counter-polling state.


#### TC 10: ifName, ifHighSpeed, and ifAlias

**Test Objective:** Verify IF-MIB name, speed, and alias track CONFIG_DB through description and LAG changes.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_interfaces.py

**Test Steps:**

1. Collect persistent CONFIG_DB facts and `show interface status` for every frontend ASIC; include physical-port aliases, PortChannels, and the management interface.
2. Walk `ifName`, `ifIndex`, `ifType`, `ifMtu`, `ifAdminStatus`, `ifOperStatus`, `ifAlias`, `ifSpeed`, and `ifHighSpeed`; require every physical alias and PortChannel from every ASIC and each management interface to be present.
3. Compare MTU (except the automated test's `eth0` exemption), description, and admin status with CONFIG_DB; compare physical-port and LAG operational status with APPL_DB. On single-ASIC DUTs, also compare management-interface fields and `MGMT_PORT_TABLE` operational status; on multi-ASIC DUTs, require only management-interface presence, matching the automated test's current limitation.
4. Require `ifType=6` for physical/management Ethernet and `ifType=161` for PortChannel, and require unique ifIndex values with the implemented index relation.
5. For each physical port, require `ifSpeed` in bps when representable; above the Counter32 maximum require `ifSpeed=4294967295` and `ifHighSpeed` equal to CONFIG_DB speed in Mbps.
6. Set `sudo config interface description Ethernet0 snmp-alias-test`; poll `ifAlias` until it changes, then restore the original description. For an existing LAG, also verify its name, statuses, MTU, description, and type.

#### TC 11: Route table lifecycle

**Test Objective:** Verify `ipRouteNextHop`, `ipCidrRouteDest`, and `ipCidrRouteStatus` follow route add/change/delete.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_default_route.py

**Test Steps:**

1. Run `show ip route 0.0.0.0/0 | grep '*'`; collect every active default-route next hop except routes through `eth0` or `Ethernet-BP`.
2. Walk `ipCidrRouteTable`. If no eligible default next hop exists, expect no `snmp_cidr_route`; otherwise require one row per eligible next hop with destination `0.0.0.0` and status `active(1)`.
3. Add a disposable route with `sudo config route add prefix 192.0.2.0/24 nexthop <nh>` and poll `ipCidrRouteDest`, next hop, and status until they match `show ip route 192.0.2.0/24`.
4. Replace the next hop where the topology permits; expect the old SNMP row to disappear and the new active row to appear.
5. Delete the route with `sudo config route del prefix 192.0.2.0/24 nexthop <nh>` and confirm no stale route row remains.

#### TC 12: ARP table lifecycle

**Test Objective:** Verify `ipNetToMediaPhysAddress` tracks neighbor add and age/delete.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Ping a directly connected neighbor to populate ARP: `ping -c 3 <neigh_ip>`.
2. Compare `show arp` / `ip neigh` with `snmpwalk ... .1.3.6.1.2.1.4.22.1.2`.
3. Confirm ifIndex and MAC encoding match APPL_DB `NEIGH_TABLE`.
4. Clear the neighbor (`sudo ip neigh del <neigh_ip> dev <if>` or wait for aging) and poll until the SNMP row is gone.
5. On multi-ASIC hardware, repeat for a neighbor in a non-default namespace.

#### TC 13: Q-BRIDGE FDB

**Test Objective:** Verify `dot1qTpFdbPort` maps VLAN+MAC to the correct ifIndex through learn, move, and age.

**Testbed:** Any

**sonic-mgmt coverage:** tests/snmp/test_snmp_fdb.py

**Test Steps:**

1. Clear prior test MACs with `sudo sonic-clear fdb all`; wait until `show mac` contains no dynamic `02:11:22:33:*` entries and require every configured PortChannel and member to be up.
2. Enumerate running VLAN member ports. From each corresponding PTF port, send one tagged 100/104-byte ICMP frame per permitted VLAN using a unique `02:11:22:33:<port>` source MAC.
3. Wait up to 40 seconds for `show mac` to learn all sent MACs, then poll SNMP facts / `dot1qTpFdbPort` for up to 60 seconds.
4. Require the SNMP dynamic-MAC count to equal the sent count; for every VLAN.MAC row, require its bridge-port ifIndex to exist in `ifTable`, and require the number mapped to PortChannels to equal the number sent through PortChannels.
5. Move one test MAC to another VLAN member and require the SNMP bridge-port mapping to follow the new interface.
6. Stop traffic, clear or age the test entries, and confirm the corresponding SNMP rows disappear.

#### TC 14: Sensor status

**Test Objective:** Verify `entPhySensorOperStatus` and sensor metadata track present, unavailable, and faulted platform sensors.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_phy_entity.py

**Test Steps:**

1. Collect `FAN_INFO`, `PSU_INFO`, `TEMPERATURE_INFO`, and `TRANSCEIVER_INFO` from STATE_DB, then walk ENTITY-MIB and ENTITY-SENSOR-MIB.
2. For fan tachometers require sensor type `unknown`, precision `0`, scale `units`, value in 1..100, and operational status `ok`, `nonoperational`, or `unavailable`.
3. For PSU current/voltage/power/temperature and chassis thermals, require the corresponding amperes/volts-DC/watts/celsius type, precision `3`, scale `units`, and one of the allowed operational statuses.
4. For transceiver temperature, voltage, bias, TX power, and RX power, verify the automated test's OID derivation, type, precision, scale, value conversion, and parent entity against STATE_DB.
5. Where supported, remove/reinsert a fan or power off/on one PSU outlet and poll both ENTITY and sensor rows; expect absent/unavailable state during the fault and restored values afterward.

#### TC 15: Cisco FRU PSU status

**Test Objective:** Verify Cisco FRU PSU status matches STATE_DB for present/OK, absent, and failed.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_psu.py

**Test Steps:**

1. Run `psuutil numpsus`; on non-VS hardware require return code 0 and require its count to equal the number of SNMP PSU rows.
2. Sort `PSU_INFO|*` STATE_DB keys naturally and walk Cisco FRU PSU status `.1.3.6.1.4.1.9.9.117.1.1.2.1.2`.
3. For each indexed PSU, compare `presence` and `status` with SNMP: present+healthy=`2`, present+failed=`7`, and absent=`8`.
4. Require at least one PSU to report healthy (`2`) before disruptive checks.
5. If the hardware/PDU permits, power off or remove one redundant PSU; poll until STATE_DB and SNMP both report failed/missing, then restore it and require status `2`.

#### TC 16: Cisco queue counter visibility

**Test Objective:** Verify SNMP exposes exactly the counters for configured buffer queues when optimized queue-counter creation is enabled.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_queue_counters.py

**Test Steps:**

1. Choose an active, non-internal interface in the selected frontend ASIC and derive its Cisco queue-counter OID `.1.3.6.1.4.1.9.9.580.1.5.5.1.4.<ifIndex>`.
2. Save the namespace-specific CONFIG_DB, set `DEVICE_METADATA|localhost create_only_config_db_buffers=true`, and identify a `BUFFER_QUEUE` range for the interface; split a single range if necessary so only a subset will be removed.
3. Reload CONFIG_DB safely, count UC/MC queue rows from `queuestat -p <interface>` (or `queuestat -n <namespace>`) and poll `docker exec snmp snmpwalk ... <queue_oid>`.
4. Require the SNMP row count to equal the `queuestat` queue count multiplied by four counters per UC queue (or the platform's UC+MC count).
5. Delete the selected `BUFFER_QUEUE` subset, reload, and require the SNMP count decrease by four or eight rows per removed queue; on Broadcom-DNX VOQ chassis require the documented static count instead.
6. Restore the saved namespace CONFIG_DB with safe reload.

#### TC 17: Cisco PFC per-priority and aggregate counters

**Test Objective:** Verify required PFC MIB objects exist and per-priority plus aggregate request/indication counters track SAI PFC counters.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_pfc_counters.py

**Test Steps:**

1. Collect SNMP interface facts and examine every physical interface whose description contains `Ethernet`; skip the Arista-7060X6 `PT0` management-port exception used by the automated test.
2. For every selected interface, require `cpfcIfRequests`, `cpfcIfIndications`, `requestsPerPriority`, and `indicationsPerPriority` to be present.
3. Enable the lab lossless/PFC profile and baseline aggregate counters plus all priorities 0..7 in SNMP and matching `SAI_PORT_STAT_PFC_<n>_TX_PKTS` / `_RX_PKTS` COUNTERS_DB fields.
4. Generate PFC on one priority from the peer/TGen; require only that priority's request/indication values to increase and match the corresponding SAI deltas.
5. Generate PFC on a second priority, then require aggregate request/indication deltas to equal the sum of all eight priority deltas; for a LAG, also compare the sum of member counters.
6. Restore the original PFC profile and confirm all required MIB fields remain present.


#### TC 18: Cisco BGP peer2 state

**Test Objective:** Verify `cbgpPeer2State` follows IPv4/IPv6 BGP session state.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. List peers with `show bgp summary` and `vtysh -c 'show bgp summary'`.
2. Walk `.1.3.6.1.4.1.9.9.187.1.2.5.1.3` and decode address type/length/octets indexes.
3. Map SNMP state to Idle/Connect/Established and compare with FRR.
4. Shut a neighbor (`sudo config bgp shutdown neighbor <peer>` or interface shutdown) and poll until SNMP leaves Established.
5. Restore BGP and confirm Established. On T2, document per-namespace limitation if only one table is exposed.

#### TC 19: Force10 CPU and memory utilization

**Test Objective:** Verify Force10 5-second, 1-minute, and 5-minute CPU utilization and memory utilization track host measurements.

**Testbed:** Any

**sonic-mgmt coverage:** `tests/snmp/test_snmp_cpu.py`, `tests/snmp/test_snmp_memory.py`

**Test Steps:**

1. Determine the DUT vCPU count with host facts or `nproc`; GET the Force10 5-second CPU OID and require a non-zero integer.
2. Start one `nohup yes > /dev/null 2>&1 &` worker per vCPU and wait 40 seconds, matching the automated load procedure.
3. GET 5-second CPU again; compare it with the rounded non-idle CPU from the second sample of `top -bn2 -d5` and require an absolute difference no greater than 5 percentage points.
4. GET the 1-minute CPU, 5-minute CPU, and memory utilization OIDs; require each value in 0..100. Also compare UCD-SNMP total/free/buffer/shared/cached memory with `/proc/meminfo`, adding `SReclaimable` to Linux `Cached`; require exact total memory and the size-dependent 4–12% tolerance used by the automated test for dynamic values.
5. Keep CPU load active beyond one minute and require the 1-minute value to rise. Run the automated `/tmp/memory.py` load on systems with more than 2 GiB and require SNMP free memory to remain within the same tolerance; stop the CPU and memory workers and confirm utilization decays.

#### TC 20: ENTITY hierarchy

**Test Objective:** Verify entPhysicalTable parent/class/name/serial relations for chassis, PSU, fan, thermal, and transceiver.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/snmp/test_snmp_phy_entity.py

**Test Steps:**

1. Collect fan-drawer, fan, PSU, thermal, and transceiver STATE_DB records, including `position_in_parent`, parent, serial, model, replaceability, and sensor fields.
2. Walk `entPhysicalTable` and ENTITY-SENSOR-MIB; derive the expected entity and sensor indexes from each component type and position exactly as the automated test does.
3. For fan drawers, fans, PSUs, thermals, and transceivers, require the expected OID and compare description, containment, physical class, relative position, name, serial, model, and `entPhysIsFRU` with STATE_DB (including expected empty unsupported fields).
4. Require fan sensors to be children of their fans, PSU sensors to be children of their PSUs, thermals to be under the chassis, and transceiver sensors to be under the correct transceiver/port entity.
5. On a PDU-backed platform, power off/on a redundant PSU and require its entity/sensor data to disappear and return; on a replaceable-fan platform, remove/reinsert a fan and require the same lifecycle.

Placeholder objects (`ifPhysAddress`, `ifLastChange`, `ifSpecific`, `ifLinkUpDownTrapEnable`, `ifPromiscuousMode`, `ifConnectorPresent`, `ifCounterDiscontinuityTime`, `entPhysicalVendorType`, `entPhysicalAlias`, `entPhysicalAssetID`) may have optional stub-contract checks. Do not treat stub values as feature coverage.

---

## gRPC (gNMI and gNOI)

### Feature description

`sonic-gnmi` exposes gNMI Capabilities, Get, Set, and Subscribe. Native DB paths read CONFIG_DB, APPL_DB, STATE_DB, and COUNTERS_DB. Writes target CONFIG_DB and selected APPL_DB data. YANG/Translib paths use SONiC or OpenConfig models. A SetRequest is one ordered transaction: delete, then replace, then update. CONFIG_DB writes are intended to roll back on failure and persist to `config_db.json`.

gNOI System, File, and OS are registered on supporting images. Ping and Traceroute return `Unimplemented`. FactoryReset, Healthz, Containerz, Debug, ORAS, SonicService/JWT, and gNSI are build-dependent and must be gated by runtime inventory.

Existing `tests/gnmi` and `tests/telemetry` cases cover Capabilities, certificate auth, CONFIG_DB incremental/full replace and subscribe, APPL_DB DASH VNET, COUNTERS_DB get/poll/sample, selected events, System Time, cold/warm reboot, OS Verify/Activate, and KillProcess. The cases below fill remaining functional gaps.

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

1. Discover the active `gnmi` or `telemetry` container and port, then call Capabilities with the mapped client CN `test.client.gnmi.sonic`.
2. Require a zero client return code and require both `sonic-db` and `JSON_IETF` in the response; also validate installed YANG model names/versions when Translib models are advertised.
3. Map the CN to `gnmi_noaccess`; call Capabilities and require failure with the role name in the error.
4. Repeat with `gnmi_readonly`, `gnmi_readwrite`, and an empty role; each must succeed and still advertise `sonic-db` plus `JSON_IETF`.
5. Restore the default CN mapping with the `add_gnmi_client_common_name` equivalent.

#### TC 2: Native Get

**Test Objective:** Verify Get of CONFIG_DB, APPL_DB, STATE_DB, and COUNTERS_DB at table, key, and field granularity.

**Testbed:** Any

**sonic-mgmt coverage:** `tests/gnmi/test_gnmi_appldb.py`, `tests/gnmi/test_gnmi_countersdb.py`

**Test Steps:**

1. GET `/sonic-db:CONFIG_DB/localhost/DEVICE_METADATA/localhost`; compare the JSON object with `sonic-db-cli CONFIG_DB hgetall 'DEVICE_METADATA|localhost'`.
2. Create `DASH_VNET_TABLE|Vnet1` through a gNMI APPL_DB update, then GET `.../DASH_VNET_TABLE/Vnet1/vni` and its `_DASH_VNET_TABLE` compatibility path; require one path to return string value `"1000"`, then delete the key and require both Gets to fail.
3. For every UC queue shown by `show queue counters Ethernet0`, GET `/sonic-db:COUNTERS_DB/localhost/COUNTERS_QUEUE_NAME_MAP/Ethernet0:<queue>` and require an `oid`; an `Ethernet0:abc` key must return a gRPC error.
4. Read Ethernet0's OID with `sonic-db-cli COUNTERS_DB hget COUNTERS_PORT_NAME_MAP Ethernet0`; GET its `/COUNTERS/<oid>` object and require `SAI_PORT_STAT_IF_IN_ERRORS`.
5. GET one known STATE_DB object and compare it field-for-field with `sonic-db-cli STATE_DB`; issue a multi-path Get spanning the available DB targets and verify prefixes, timestamps, and JSON_IETF types.

#### TC 3: Virtual and OTHERS Get

**Test Objective:** Verify documented virtual/OTHERS telemetry paths match Linux or Redis sources.

**Testbed:** Any

**sonic-mgmt coverage:** tests/telemetry/test_telemetry.py

**Test Steps:**

1. GET target `OTHERS`, path `osversion/build`; require exactly one `build_version` beginning `SONiC.` and reject `SONiC.NA`.
2. GET target `OTHERS`, path `proc/uptime`; parse `total` as a float, wait 10 seconds, repeat, and require an increase of at least 10 seconds.
3. GET target `COUNTERS_DB`, path `COUNTERS/Ethernet0`; require `SAI_PORT_STAT_IF_IN_ERRORS` in the response.
4. Subscribe to the default virtual-DB Ethernet0 path for three updates; require one completion marker, three response timestamps, and three Ethernet0 updates.
5. Subscribe ON_CHANGE to namespace `STATE_DB/NEIGH_STATE_TABLE`, modify a real BGP neighbor's state in that namespace, and require the neighbor key in the update before restoring its original state.
6. With `create_only_config_db_buffers=true`, query `COUNTERS_QUEUE_NAME_MAP`, remove one configured buffer queue, reload, and require the returned queue count to decrease before restoring CONFIG_DB.

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

1. Select the first admin-up physical interface from `show interface status`; skip supervisor nodes without front-panel PORT data.
2. Write JSON string `"down"` on the PTF and update `/sonic-db:CONFIG_DB/localhost/PORT/<interface>/admin_status` with gNMI Set.
3. Require `sonic-db-cli CONFIG_DB hget 'PORT|<interface>' admin_status` and gNMI Get of the same leaf both return `down`.
4. Update the leaf to `up`; require CONFIG_DB and gNMI Get both return `up`, then require all configured BGP neighbors to re-establish within 60 seconds.
5. Update invalid path `/sonic-db:CONFIG_DB/localhost/PORTABC/Ethernet100/admin_status`; require the Set helper to raise an error rather than silently create a table.

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

#### TC 10: Full CONFIG_DB replace and persistence

**Test Objective:** Verify full CONFIG_DB replace persists across config reload.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. Select the first admin-up physical interface; dump full CONFIG_DB with `sonic-cfggen -d --print-data` (or the owning frontend namespace on multi-ASIC) and require the PORT/admin_status fields exist.
2. Change only that interface's `admin_status` to `down` in the JSON dump and replace `/sonic-db:CONFIG_DB/localhost/` with the full document.
3. Poll CONFIG_DB for up to 30 seconds and require the interface to become `down`; verify unrelated top-level tables from the dump remain present.
4. Run `sudo config save -y`, then `sudo config reload -y`; require the replaced value to survive and critical services to recover.
5. Restore the interface with `sudo config interface startup <interface>`, save, and require all BGP neighbors to re-establish.

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

**sonic-mgmt coverage:** `tests/gnmi/test_gnmi_configdb.py`, `tests/gnmi/test_gnmi_countersdb.py`

**Test Steps:**

1. For each CONFIG_DB path level—`DEVICE_METADATA` table, `localhost` key, and `bgp_asn` field—start a SAMPLE subscription using `gnmi_subscribe_streaming_sample`.
2. Request at least five updates and require `bgp_asn` to appear at least five times for each path level.
3. Repeat for COUNTERS_DB `COUNTERS_PORT_NAME_MAP` at table and Ethernet0-key granularity; require at least three responses containing an `oid`.
4. Subscribe to the Ethernet0 `SAI_PORT_STAT_IF_IN_ERRORS` field and require it in at least three sampled updates.
5. Extend the automated checks with a one-second sample interval, `suppress_redundant`, heartbeat, and TARGET_DEFINED; verify cadence and that heartbeat updates are not treated as data changes.

#### TC 13: Subscribe ON_CHANGE

**Test Objective:** Verify ON_CHANGE initial sync and ordered create/update/delete notifications.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. For each CONFIG_DB path level—`DEVICE_METADATA`, key `localhost`, and field `bgp_asn`—start an ON_CHANGE subscription.
2. In a parallel worker, alternately delete `bgp_asn` and set it to incrementing values every 0.5 seconds.
3. Require at least five `bgp_asn` notifications for each path-level subscription, with ordered delete/update changes and no stale value after recreation.
4. For the table-level subscription, parse every `json_ietf_val`; require at least three objects and require each to contain key `localhost` with field `bgp_asn`.
5. Stop the worker and restore the original `bgp_asn` value.

#### TC 14: Subscribe POLL

**Test Objective:** Verify POLL initial sync, one snapshot per poll, and errors for invalid poll sequencing.

**Testbed:** Any

**sonic-mgmt coverage:** `tests/gnmi/test_gnmi_configdb.py`, `tests/gnmi/test_gnmi_countersdb.py`

**Test Steps:**

1. Subscribe in POLL mode to CONFIG_DB `DEVICE_METADATA` at table, key, and `bgp_asn` field granularity; issue three polls with a one-second interval.
2. For each path, require `bgp_asn` in at least three poll responses.
3. Repeat for COUNTERS_DB `COUNTERS_PORT_NAME_MAP` table and Ethernet0 key; require `oid` in at least three responses.
4. Poll COUNTERS at table, Ethernet0 OID-key, and `SAI_PORT_STAT_IF_IN_ERRORS` field granularity; require that field in every poll series and compare it with COUNTERS_DB.
5. Send a poll before a subscription or a malformed poll and require a canonical gRPC error, then close the valid stream.

#### TC 15: Certificate rotation and service lifecycle

**Test Objective:** Verify telemetry remains running without certificates and begins rejecting or accepting requests as certificates are removed, restored, or rotated.

**Testbed:** Any

**sonic-mgmt coverage:** tests/telemetry/test_telemetry_cert_rotation.py

**Test Steps:**

1. Stop the telemetry service, archive its certificates, restart it, and require `is_service_fully_started` within 100 seconds even though authenticated requests cannot succeed.
2. Restore certificates, wait for the gNMI TCP port, and issue target `OTHERS` Get for `proc/uptime`; require success within 30 seconds.
3. Archive certificates while the service is running and repeat the same Get; require a non-zero client return code, then restore certificates and wait for the port.
4. Archive certificates before a request, require the initial Get to fail, rotate certificates, wait for the port, and require the same Get to succeed.
5. With working certificates, require a Get before and after a second certificate rotation to succeed, proving rotation does not require a server restart.

#### TC 16: EVENTS subscribe

**Test Objective:** Verify SONiC EVENTS for BGP/link with filters, heartbeat, and cache options.

**Testbed:** Any

**sonic-mgmt coverage:** tests/telemetry/test_events.py

**Test Steps:**

1. Start the EVENTS suite with eventd healthy and telemetry configured for on-change events without cache.
2. Run the host, SWSS, DHCP-relay, BGP, and other available `*_events.py` publishers; for each, require the expected event payload and validate it against its YANG model using `validate_yang_events.py`.
3. For BGP/link cases, trigger the documented state transition and require the corresponding event key and fields rather than accepting any event.
4. Exercise event filters, heartbeat, and cache options and require unrelated events to be excluded.
5. Reset event counters, restart eventd, publish enough synthetic BGP events to overflow the default cache, and require `missed_to_cache` to increase by the expected threshold.

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

1. Map `test.client.gnmi.sonic` to `gnmi_noaccess`; require Capabilities to fail and include the role name. Require Capabilities with `gnmi_readonly`, `gnmi_readwrite`, and an empty role to succeed with `sonic-db` and `JSON_IETF`.
2. Delete the valid CN mapping, add only `invalid.cname`, and attempt an APPL_DB DASH_VNET update with the original certificate; require `Unauthenticated` and gNMI log text `Failed to retrieve cert common name mapping`.
3. Restore the valid mapping, serve the CRL from PTF, and repeat the APPL_DB write with `gnmiclient.revoked`; require `Unauthenticated` and log text `desc = Peer certificate revoked` (retry only transient CRL-download failures).
4. Extend with wrong-CA, expired, CN/SAN-mismatch, and optional password/JWT cases; require only configured mechanisms to succeed.
5. Stop the CRL server and restore the default client-CN mapping.

#### TC 19: Authorization matrix

**Test Objective:** Verify certificate roles restrict Get/Set/Subscribe by target.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. For CN role `gnmi_config_db_noaccess`, attempt CONFIG_DB Set, Get, and SAMPLE Subscribe; require Set/Get errors containing the role, and subscription output containing both `GRPC error` and the role.
2. For `gnmi_config_db_readwrite`, Set `DEVICE_METADATA|localhost cloudtype` to `Public`; require success. Require Get and Subscribe to succeed, with Subscribe output containing `cloudtype`.
3. For `gnmi_config_db_readonly`, require Set to fail with the role name while Get and Subscribe succeed and return `cloudtype`.
4. For an empty role, require Set to fail with `write access` while Get and Subscribe succeed.
5. Restore the default CN role and restore the original `cloudtype` value.

#### TC 20: Negative Get/Set

**Test Objective:** Verify unknown target/origin, wrong encoding, oversize payload, and unavailable DB return canonical errors.

**Testbed:** Any

**sonic-mgmt coverage:** tests/gnmi/test_gnmi_configdb.py

**Test Steps:**

1. Write JSON `"down"` and Set invalid path `/sonic-db:CONFIG_DB/localhost/PORTABC/Ethernet100/admin_status`; require the client helper to raise an exception and require no `PORTABC` table in CONFIG_DB.
2. GET an unknown target (`NOT_A_DB`) and an invalid COUNTERS queue key (`Ethernet0:abc`); require canonical gRPC errors.
3. SET a non-JSON payload on a JSON_IETF path and then an oversized payload; require bounded rejection without a partial CONFIG_DB write.
4. GET a missing table/key; if Redis-unavailable handling is tested, stop Redis only in an isolated lab and require an unavailable error before restoring it.
5. Require the gNMI service to remain running and a subsequent valid Get to succeed.

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

**sonic-mgmt coverage:** `tests/gnmi/test_gnoi_system.py`, `tests/gnmi/test_gnoi_system_grpc.py`

**Test Steps:**

1. Record DUT epoch seconds with `date +%s` and convert it to nanoseconds.
2. Call System.Time through `gnoi_request`; require return code 0, extract valid JSON from the response, and require a `time` field.
3. Create a fresh authenticated gRPC channel and `SystemStub`, send `TimeRequest`, and require the response timestamp to differ from the recorded DUT time by less than 60 seconds.
4. Call Time twice more and require monotonically increasing nanosecond values.
5. Close the gRPC channel after the check to avoid shared SSL state or resource leakage.

#### TC 2: Reboot request validation

**Test Objective:** Verify invalid reboot methods and delayed reboot are rejected without rebooting.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Record uptime: `uptime -s` / `cat /proc/uptime`.
2. Send Reboot with method UNKNOWN (0) and POWERUP (7); expect InvalidArgument.
3. Send Reboot with `delay > 0`; expect InvalidArgument.
4. Confirm uptime is unchanged and `show reboot-cause` did not record a new gNOI reboot.
5. Do not send COLD/WARM in this case (covered by TC 3).

#### TC 3: COLD and WARM reboot lifecycle

**Test Objective:** Verify accepted COLD and WARM reboot requests, RebootStatus fields, actual reboot, and service recovery.

**Testbed:** Hardware

**sonic-mgmt coverage:** tests/gnmi/test_gnoi_system_reboot.py

**Test Steps:**

1. Record DUT uptime, then send System.Reboot with message `gnoi test reboot` and method COLD; require gNOI return code 0.
2. Immediately call RebootStatus and require `active=true`, reason `gnoi test reboot`, method COLD, positive integer `when`, and integer `count >= 1`.
3. Wait for startup with a 20-second initial delay and 600-second timeout, then require all critical processes to be running.
4. Re-apply gNMI certificates (the automated test's post-reboot workaround), compare uptime timestamps, and require evidence that the DUT rebooted.
5. Repeat with method WARM; require the same status-field checks with method WARM and require startup plus critical-process recovery.

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

1. For each running allowlisted service (`snmp`, `dhcp_relay`, `radv`, `restapi`, `lldp`, `sshd`, `swss`, `pmon`, `rsyslog`, and `telemetry`), send KillProcess with `signal:1`; require return code 0 and require the service/container to stop.
2. Send KillProcess for that service with `restart:true, signal:1`; require return code 0 and require the host service to run again.
3. Send names `gnmi`, `nonexistent`, and empty string; require failure with the exact D-Bus unsupported/no-service message used by the automated parameter matrix.
4. Send invalid or empty `restart` values and require failure with `panic` in the response, matching the automated assertion; send `signal:2` and require `KillProcess only supports SIGNAL_TERM (option 1)`.
5. After every parameter case, wait for critical processes and require `critical_services_fully_started`; retain the suite's skip when a selected service was not initially running.

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

1. Call OS.Verify; require return code 0, parse response JSON, and require a `version` field.
2. Compare `version` exactly with the current image from `image_facts` / `sonic-installer list`.
3. Call OS.Activate with `invalid-image-name`; require transport return code 0 but response variant `ActivateError` containing `Image does not exist`.
4. Call OS.Activate with the exact current image name; require return code 0 and response variant `ActivateOk`.
5. Confirm the current/next image list is unchanged by these non-install checks.

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

#### TC 15: gNOI RBAC

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

1. GET `/.well-known/host-meta`; expect HTTP 200, an XRD media type/body, and a RESTCONF link whose `href` resolves to `/restconf`.
2. GET `/restconf/yang-library-version` with `Accept: application/yang-data+json`; expect 200, that media type, and the YANG-library version implemented by the image.
3. GET `/restconf/data/ietf-yang-library:modules-state`; expect 200 and a non-empty module list. For sampled modules, require name, revision (when versioned), namespace, conformance type, and a usable schema/model URL.
4. GET `/restconf/data/ietf-restconf-monitoring:restconf-state/capabilities`; expect 200 and require the RESTCONF base capability plus every query capability later exercised (`depth`, `content`, or `fields`).
5. GET `/restconf/operations`; expect 200 and a well-formed operations container whose RPC names belong to modules in the YANG library.
6. Download one advertised schema from `/models/yang/<module>.yang`; expect 200 and YANG text whose `module` name matches the modules-state entry. Any advertised URL that returns 404 is a failure.

#### TC 3: GET data

**Test Objective:** Verify GET of containers, lists, leaves, config, and state matches CLI/Redis.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `/restconf/data/openconfig-interfaces:interfaces` with `Accept: application/yang-data+json`; expect HTTP 200, that response media type, and a non-empty `interface` list.
2. GET keyed resource `/restconf/data/openconfig-interfaces:interfaces/interface=Ethernet0`; expect 200 and exactly one interface whose key/name is `Ethernet0`; an unknown key must return 404 with `ietf-restconf:errors`.
3. GET leaf `/restconf/data/sonic-port:sonic-port/PORT/PORT_LIST=Ethernet0/admin_status`; expect 200, a schema-valid JSON leaf value (`up` or `down`), and no unrelated list entries.
4. Compare the returned config values with `show interfaces status Ethernet0` and `sonic-db-cli CONFIG_DB hget 'PORT|Ethernet0' admin_status`; require exact agreement after allowing normal propagation time.
5. GET OpenConfig `state/oper-status`; expect 200 and compare it with `sonic-db-cli APPL_DB hget 'PORT_TABLE:Ethernet0' oper_status` / `show interfaces status`; require equivalent UP/DOWN values.
6. GET the parent container and selected list/leaf URLs twice; require stable keys and JSON types, while allowing timestamps/counters to change.

#### TC 4: HEAD

**Test Objective:** Verify HEAD returns GET status/headers with an empty body.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. HEAD `curl -sk -u admin:<password> -I -H 'Accept: application/yang-data+json' https://<mgmt_ip>/restconf/data/openconfig-interfaces:interfaces/interface=Ethernet0`.
2. GET the same URL and compare status and Content-Type.
3. Confirm HEAD body is empty.
4. HEAD a missing resource and confirm 404.
5. Confirm Content-Length is present on HEAD of an existing resource.

#### TC 5: OPTIONS

**Test Objective:** Verify OPTIONS Allow/Accept-Patch match the YANG node type.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. `curl -sk -u admin:<password> -X OPTIONS -D - https://<mgmt_ip>/restconf/data/openconfig-interfaces:interfaces`.
2. Confirm `Allow` includes GET and write methods as applicable.
3. OPTIONS a config leaf that supports PATCH and confirm `Accept-Patch: application/yang-data+json`.
4. OPTIONS `/restconf/operations` and a state-only node; confirm write methods are absent where required.
5. OPTIONS an unknown path and confirm 404 or documented Allow.

#### TC 6: POST create

**Test Objective:** Verify POST creates a disposable object and duplicate POST conflicts.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. POST a VLAN (or loopback) JSON to the appropriate list URL, for example `/restconf/data/sonic-vlan:sonic-vlan`.
2. Expect 201 and optional Location.
3. Confirm `show vlan brief` and CONFIG_DB `VLAN|Vlan4094`.
4. POST the same object again and expect 409 with `ietf-restconf:errors`.
5. DELETE the VLAN (`sudo config vlan del 4094` or REST DELETE).

#### TC 7: PUT replace

**Test Objective:** Verify PUT create/replace and defaulting of omitted non-default leaves.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PUT admin_status/mtu for Ethernet0 with a full config object.
2. GET the resource and compare with CONFIG_DB.
3. PUT again omitting a non-default leaf and confirm documented default/replace behavior (no unintended sibling deletion).
4. Confirm `show interfaces status Ethernet0`.
5. Restore original MTU/admin_status with `sudo config interface ...`.

#### TC 8: PATCH merge

**Test Objective:** Verify PATCH changes only supplied leaves and is idempotent.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH `curl -sk -u admin:<password> -X PATCH -H 'Content-Type: application/yang-data+json' -d '{"sonic-port:admin_status":"down"}' https://<mgmt_ip>/restconf/data/sonic-port:sonic-port/PORT/PORT_LIST=Ethernet0/admin_status`.
2. Confirm `show interfaces status Ethernet0` is down.
3. PATCH the same payload again and confirm still down (idempotent).
4. PATCH admin_status to `up`.
5. Confirm BGP/neighbors recover if the port was in use, or use a spare port.

#### TC 9: YANG Patch

**Test Objective:** Verify YANG Patch multi-edit success and atomic failure.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Send `PATCH` with `Content-Type: application/yang-patch+json` containing two valid edits (for example two descriptions).
2. Confirm both CONFIG_DB leaves changed and yang-patch-status `ok`.
3. Send a patch where the second edit is invalid.
4. Confirm per-edit status and that the first edit was rolled back if atomicity is advertised.
5. Restore descriptions.

#### TC 10: DELETE

**Test Objective:** Verify DELETE of leaf/list/container and repeated DELETE.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Create Vlan4094 via POST or `sudo config vlan add 4094`.
2. DELETE `curl -sk -u admin:<password> -X DELETE https://<mgmt_ip>/restconf/data/sonic-vlan:sonic-vlan/VLAN/VLAN_LIST=Vlan4094`.
3. Confirm `show vlan brief` has no 4094.
4. DELETE again and expect 404 / data-missing.
5. If capabilities include `deleteEmptyEntry`, DELETE with `?deleteEmptyEntry=true` on a parent and confirm empty-parent cleanup.

#### TC 11: RPC operations

**Test Objective:** Verify POST to `/restconf/operations` for a safe RPC and structured errors.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET `/restconf/operations` and pick a safe RPC (for example `sonic-show-techsupport` if listed).
2. POST valid input JSON to `/restconf/operations/<rpc>`.
3. Confirm 200 and any output file/log (`ls /var/dump` or documented path).
4. POST malformed JSON and an unknown RPC name; expect 400/404 and `ietf-restconf:errors`.
5. Skip if no safe RPC is advertised.

#### TC 12: Query parameters

**Test Objective:** Verify `depth`, `content`, and `fields` filter GET subtrees.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET interfaces with `?depth=1` and without `depth`; confirm the shallow response is smaller.
2. GET `?content=config` and `?content=nonconfig` (or `all`) and compare config vs state leaves.
3. GET `?fields=interface/name` (or the advertised fields syntax) and confirm only requested nodes.
4. Confirm capabilities include the corresponding URNs from TC 2.
5. If the image ignores query parameters, record not-supported rather than fail.

#### TC 13: Invalid query parameters

**Test Objective:** Verify unknown or illegal query parameters return 400/405.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET with `?depth=0` or `?depth=abc`.
2. GET with `?fields=...&depth=1` if the combination is illegal.
3. GET with `?notAParam=1`.
4. DELETE with `?depth=1`.
5. Confirm 400/405 and `ietf-restconf:errors`; confirm no config change.

#### TC 14: Basic authentication

**Test Objective:** Verify HTTP Basic succeeds only with a valid admin-capable user.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. GET with `-u admin:<password>` and expect 200.
2. GET without `-u` and expect 401.
3. GET with `-u admin:wrong` and expect 401.
4. If a non-admin user exists, GET may succeed and PATCH must return 403.
5. Confirm `sonic-db-cli CONFIG_DB hget 'REST_SERVER|default' client_auth` includes password/user.

#### TC 15: JWT authentication

**Test Objective:** Verify JWT issue/use/expiry if `/authenticate` exists.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. POST `https://<mgmt_ip>/authenticate` with username/password JSON.
2. If 404, skip as not supported.
3. GET RESTCONF with `Authorization: Bearer <token>` and expect 200.
4. GET with a tampered/expired token and expect 401.
5. Do not require JWT on images that only support Basic/cert.

#### TC 16: Client certificate authentication

**Test Objective:** Verify cert-mode RESTCONF accepts only mapped trusted certificates.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Set `sonic-db-cli CONFIG_DB hset 'REST_SERVER|default' client_auth cert` and install `ca_crt`/`server_crt`.
2. Restart with `sudo systemctl restart mgmt-framework`.
3. GET with `--cert client.crt --key client.key` and expect 200.
4. GET with an unknown/revoked client cert and expect failure.
5. Restore `client_auth` to `user` and restart mgmt-framework.

#### TC 17: Authorization

**Test Objective:** Verify readonly/noaccess cannot write; admin writes succeed.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH admin_status as admin and confirm CONFIG_DB changes.
2. PATCH as a non-admin user and expect 403 with no CONFIG_DB change.
3. GET as readonly should succeed if that role exists.
4. POST a VLAN as noaccess/readonly and expect 403.
5. Restore Ethernet0 admin_status with `sudo config interface startup Ethernet0`.

#### TC 18: Media type and schema errors

**Test Objective:** Verify wrong Content-Type and invalid JSON/YANG data return 400/415 without commit.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH with `Content-Type: text/plain` and expect 415.
2. PATCH malformed JSON and expect 400.
3. PATCH `admin_status` to `not-a-status` and expect 400 invalid-value.
4. Confirm CONFIG_DB admin_status unchanged.
5. PATCH with wrong module prefix and expect 400.

#### TC 19: Cross-NBI consistency

**Test Objective:** Verify RESTCONF writes are visible in CLI/Redis/gNMI and the reverse.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH Ethernet0 description via RESTCONF.
2. Confirm `show interfaces status Ethernet0` and `sonic-db-cli CONFIG_DB hget 'PORT|Ethernet0' description`.
3. If gNMI is enabled, GET the same leaf via `/sonic-db:CONFIG_DB/.../description`.
4. Set description via `sudo config interface description Ethernet0 from-cli` and GET via RESTCONF.
5. Clear the description.

#### TC 20: Persistence

**Test Objective:** Verify RESTCONF config survives mgmt-framework restart and config reload.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. PATCH a disposable description and `sudo config save -y`.
2. Restart `sudo systemctl restart mgmt-framework` and GET the description.
3. Run `sudo config reload -y`, wait for services, and GET again.
4. Confirm state-only data was not persisted.
5. Clear the description.

#### TC 21: Concurrency

**Test Objective:** Verify parallel GETs succeed and overlapping writes do not tear state.

**Testbed:** Any

**sonic-mgmt coverage:** Not covered

**Test Steps:**

1. Run 10 parallel GETs of `/openconfig-interfaces:interfaces`.
2. Run two parallel PATCHes of the same leaf to different values.
3. Read CONFIG_DB and confirm a single final value.
4. If 409 in-use is returned, confirm it is deterministic.
5. Restore the leaf.


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
