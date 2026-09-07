# SONiC SAG Test Plan HLD

## Revision History

[© Edgecore Networks](https://www.edge-core.com/)

| Revision No | Description | Author | Contributors | Date |
| :-------------: |:-------------:| :-----:|:----------:|:-----:|
|1.0| SAG Test Cases Version 1.0| [Jimi Chen](https://github.com/superchild), [Josh Yang](https://github.com/JoshYangEC)| [Jimi Chen](https://github.com/superchild), [Josh Yang](https://github.com/JoshYangEC) | 8 April 2022|
|2.0| Extend with SAG HLD system tests, dual-VTEP, Asymmetric IRB, VXLAN, failure and negative cases | Cursor Agent | | 25 August 2026|

## Table of Contents

- Overview
- Scope
- Introduction
- Testbed
- Experimental Setup Configuration
- Test Cases
  - Test Cases for IPv4 (baseline SAG)
  - Test Cases for IPv6 (baseline SAG)
  - System Test Cases (from SAG HLD)
  - Dual VTEP and Asymmetric IRB Test Cases
  - Failure and Negative Test Cases

## Overview

This document describes Static Anycast Gateway (SAG) functional testing in a SONiC test environment.

SAG is the anycast default-gateway addressing mechanism used with EVPN/VXLAN. The same gateway IP and virtual MAC are configured on every leaf (VTEP) so hosts always ARP/ND the local leaf. SAG is advertised only on host-facing ports, not toward the fabric.

Asymmetric IRB is the overlay forwarding model typically paired with SAG:

- **Forward path:** the ingress leaf routes between VLANs and encapsulates using the **destination VLAN VNI**.
- **Reverse path:** the remote leaf routes locally and encapsulates using the **source VLAN VNI**.
- Bridging of same-VLAN traffic stays L2 (no routing) and uses the L2 VNI.

## Scope

- SAG ping, routing, MAC change, enable/disable, and config reload (IPv4 and IPv6)
- System-level programming checks from the [SAG HLD](https://github.com/sonic-net/SONiC/blob/master/doc/sag/sag-HLD.md) (kernel, ASIC, IP2ME trap, neighbor learning)
- Same SAG IP/MAC on two VTEPs
- Host ARP/ND to SAG
- Same-VLAN L2 forwarding
- Local and remote inter-VLAN routing
- Asymmetric IRB forward and reverse directions
- VXLAN VNI encapsulation and decapsulation
- Leaf failure with continuous traffic, EVPN/BGP failure, MAC mobility
- Negative cases: VLAN/VNI mismatch and missing EVPN route / unreachable VTEP

## Introduction

SAG provides a default gateway (IPv4 and IPv6) using a global virtual MAC and a per-VLAN enable knob.

```
config static-anycast-gateway mac_address add <mac>
config vlan static-anycast-gateway enable <vlan_id>
```

When SAG is enabled on a VLAN interface, that interface uses the SAG MAC instead of the system CPU MAC. IP addresses on the VLAN interface are unchanged.

## Testbed

Baseline SAG cases run on [PTF32](https://github.com/Azure/sonic-mgmt/blob/master/ansible/vars/topo_ptf32.yml) or [PTF64](https://github.com/Azure/sonic-mgmt/blob/master/ansible/vars/topo_ptf64.yml), and on `t0` / `t0-2vlans` using existing VLAN member ports.

Dual-VTEP, VXLAN, and Asymmetric IRB cases run on:

- `t0` / `t0-2vlans`: DUT is VTEP1; PTF simulates attached hosts and remote VTEP2
- `dualtor` / `dualtor-aa`: two DUTs are leaf VTEPs sharing the same SAG IP/MAC

### Supported Topology

`ptf32`, `ptf64`, `t0`, `t0-2vlans`, `dualtor`, `dualtor-aa`

## Experimental Setup Configuration

### Baseline SAG (single DUT)

![SAG Topology](./Img/SAG_Topology.png)

### Dual VTEP + Asymmetric IRB

```text
                    +------------------+
  Host-A VLAN10     |     VTEP1/DUT    |     VXLAN (VNI=dest VLAN)
  ARP -> SAG MAC/IP |  SAG IP + MAC    |<-------------------------> VTEP2 (DUT or PTF)
                    |  VLAN10 / VLAN20 |     underlay BGP/EVPN
  Host-B VLAN20     |                  |
                    +------------------+

Forward (A in VLAN10 -> B in VLAN20):
  Host-A --L2--> SAG on VTEP1 --route to VLAN20-- VXLAN encap VNI(VLAN20) --> VTEP2 --L2--> Host-B

Reverse (B in VLAN20 -> A in VLAN10):
  Host-B --L2--> SAG on VTEP2 --route to VLAN10-- VXLAN encap VNI(VLAN10) --> VTEP1 --L2--> Host-A

Same VLAN (A1 and A2 in VLAN10):
  Host-A1 --L2 bridge (no SAG routing)-- VXLAN VNI(VLAN10) if remote --L2--> Host-A2
```

Both VTEPs MUST use the **same** SAG IPv4/IPv6 addresses and the **same** global SAG MAC on the corresponding VLAN interfaces.

## Test Cases

## Test Cases for IPv4

The following test cases #1 ~ #5 are discussed and verified using IPv4 address for SAG.

### Test Case # 1: Testing pingable of SAG using CLI to setup SAG

- Test steps

  1. setup host1 in eth0 of ptf
  2. config mac address of SAG1 using CLI
  3. config ipv4 address of SAG1 using CLI
  4. enable SAG using CLI
  5. flush all neighbor of host1 and then let host1 ping SAG1
  6. verify SAG in the neighbor of host1

- Pass/Fail Criteria

  - Test case is pass if SAG1 is found as neighbor of host1 after step 6

### Test Case # 2: Testing routing over SAG using CLI to setup SAG

- Test steps

  1. setup host1 and host2 in eth0 and eth1 of ptf respectively
  2. config mac address of SAG1 using CLI
  3. config ipv4 address of SAG1 using CLI
  4. enable SAG using CLI
  5. If the DUT has only one IPv4 VLAN, create VLAN2: move one downlink out of VLAN1, `config vlan add`, add the port untagged, and `config interface ip add` a free IPv4 prefix; enable SAG on VLAN2
  6. config ipv4 address of vlan interface (VLAN2) when it is not already present
  7. flush all neighbor of host2 and then let host2 ping VLAN2
  8. flush all neighbor of host1 and then let host1 ping host2
  9. verify SAG in the neighbor of host1
  10. restore VLAN membership if VLAN2 was created for the test

- Pass/Fail Criteria
  - Test case is pass if host2 is pingable after step 8
  - Test case is pass if SAG1 is found as neighbor of host1 after step 9

### Test Case # 3: Testing pingable and reachable over SAG after changing mac address using CLI to setup SAG

- Test steps
  1. setup host1 and host2 in eth0 and eth1 of ptf respectively
  2. config mac address (MAC1) of SAG1 using CLI
  3. config ipv4 address of SAG1 using CLI
  4. enable SAG using CLI
  5. config ipv4 address of vlan interface (VLAN2)
  6. flush all neighbor of host1 and then let host1 ping host2
  7. verify neighbor of host1
  8. change mac address of SAG1 to MAC2 using CLI
  9. flush all neighbor of host1 and then let host1 ping host2
  10. verify neighbor of host1

- Pass/Fail Criteria
  - Test case is pass if host2 is pingable after step 6
  - Test case is pass if SAG1 is found as neighbor of host1 after step 7 before changing mac
  - Test case is pass if host2 is pingable after step 9
  - Test case is pass if SAG1 is found as neighbor of host1 after step 10 after changing mac address of SAG1

### Test Case # 4: Testing pingable and reachable over SAG after disabled and then enabled SAG using CLI to setup SAG

- Test steps
  1. setup host1 and host2 in eth0 and eth1 of ptf respectively
  2. config mac address of SAG1 using CLI
  3. config ipv4 address of SAG1 using CLI
  4. config ipv4 address of vlan interface (VLAN2)
  5. enable SAG using CLI
  6. disable SAG using CLI
  7. flush all neighbor of host1 and then let host1 ping SAG1
  8. verify neighbor of host1
  9. flush all neighbor of host1 and then let host1 ping host2
  10. verify neighbor of host1
  11. enable SAG using CLI
  12. flush all neighbor of host1 and then let host1 ping SAG1
  13. verify neighbor of host1
  14. flush all neighbor of host1 and then let host1 ping host2
  15. verify neighbor of host1

- Pass/Fail Criteria
  - Test case is pass if SAG1 is not found as neighbor of host1 after step 8
  - Test case is pass if host2 is not pingable after step 9
  - Test case is pass if SAG1 is not found as neighbor of host1 after step 10
  - Test case is pass if SAG1 is found as neighbor of host1 after step 13
  - Test case is pass if host2 is pingable after step 14
  - Test case is pass if SAG1 is found as neighbor of host1 after step 15

### Test Case # 5: Testing pingable and reachable over SAG after config reload using CLI to setup SAG

- Test steps
  1. setup host1 and host2 in eth0 and eth1 of ptf respectively
  2. config mac address of SAG1 using CLI
  3. config ipv4 address of SAG1 using CLI
  4. enable SAG using CLI
  5. config ipv4 address of vlan interface (VLAN2)
  6. config reload
  7. flush all neighbor of host1 and then let host1 ping SAG1
  8. verify neighbor of host1

- Pass/Fail Criteria
  - Test case is pass if SAG1 is found as neighbor of host1 after step8

## Test Cases for IPv6

The following test cases #1 ~ #5 are discussed and verified using IPv6 address for SAG.

### Test Case # 1: Testing pingable of SAG using CLI to setup SAG

- Test steps

  1. setup host1 in eth0 of ptf
  2. config mac address of SAG1 using CLI
  3. config ipv6 address of SAG1 using CLI
  4. enable SAG using CLI
  5. flush all neighbor of host1 and then let host1 ping SAG1
  6. verify SAG in the neighbor of host1

- Pass/Fail Criteria

  - Test case is pass if SAG1 is found as neighbor of host1 after step 6

### Test Case # 2: Testing routing over SAG using CLI to setup SAG

- Test steps

  1. setup host1 and host2 in eth0 and eth1 of ptf respectively
  2. config mac address of SAG1 using CLI
  3. config ipv6 address of SAG1 using CLI
  4. enable SAG using CLI
  5. config ipv6 address of vlan interface (VLAN2)
  6. flush all neighbor of host2 and then let host2 ping VLAN2
  7. flush all neighbor of host1 and then let host1 ping host2
  8. verify SAG in the neighbor of host1

- Pass/Fail Criteria
  - Test case is pass if host2 is pingable after step 7
  - Test case is pass if SAG1 is found as neighbor of host1 after step 8

### Test Case # 3: Testing pingable and reachable over SAG after changing mac address using CLI to setup SAG

- Test steps
  1. setup host1 and host2 in eth0 and eth1 of ptf respectively
  2. config mac address (MAC1) of SAG1 using CLI
  3. config ipv6 address of SAG1 using CLI
  4. enable SAG using CLI
  5. config ipv6 address of vlan interface (VLAN2)
  6. flush all neighbor of host1 and then let host1 ping host2
  7. verify neighbor of host1
  8. change mac address of SAG1 to MAC2 using CLI
  9. flush all neighbor of host1 and then let host1 ping host2
  10. verify neighbor of host1

- Pass/Fail Criteria
  - Test case is pass if host2 is pingable after step 6
  - Test case is pass if SAG1 is found as neighbor of host1 after step 7 before changing mac
  - Test case is pass if host2 is pingable after step 9
  - Test case is pass if SAG1 is found as neighbor of host1 after step 10 after changing mac address of SAG1

### Test Case # 4: Testing pingable and reachable over SAG after disabled and then enabled SAG using CLI to setup SAG

- Test steps
  1. setup host1 and host2 in eth0 and eth1 of ptf respectively
  2. config mac address of SAG1 using CLI
  3. config ipv6 address of SAG1 using CLI
  4. config ipv6 address of vlan interface (VLAN2)
  5. enable SAG using CLI
  6. disable SAG using CLI
  7. flush all neighbor of host1 and then let host1 ping SAG1
  8. verify neighbor of host1
  9. flush all neighbor of host1 and then let host1 ping host2
  10. verify neighbor of host1
  11. enable SAG using CLI
  12. flush all neighbor of host1 and then let host1 ping SAG1
  13. verify neighbor of host1
  14. flush all neighbor of host1 and then let host1 ping host2
  15. verify neighbor of host1

- Pass/Fail Criteria
  - Test case is pass if SAG1 is not found as neighbor of host1 after step 8
  - Test case is pass if host2 is not pingable after step 9
  - Test case is pass if SAG1 is not found as neighbor of host1 after step 10
  - Test case is pass if SAG1 is found as neighbor of host1 after step 13
  - Test case is pass if host2 is pingable after step 14
  - Test case is pass if SAG1 is found as neighbor of host1 after step 15

### Test Case # 5: Testing pingable and reachable over SAG after config reload using CLI to setup SAG

- Test steps
  1. setup host1 and host2 in eth0 and eth1 of ptf respectively
  2. config mac address of SAG1 using CLI
  3. config ipv6 address of SAG1 using CLI
  4. enable SAG using CLI
  5. config ipv6 address of vlan interface (VLAN2)
  6. config reload
  7. flush all neighbor of host1 and then let host1 ping SAG1
  8. verify neighbor of host1

- Pass/Fail Criteria
  - Test case is pass if SAG1 is found as neighbor of host1 after step8

## System Test Cases (from SAG HLD)

These cases implement the [System Test Cases](https://github.com/sonic-net/SONiC/blob/master/doc/sag/sag-HLD.md#system-test-cases) section of the SAG HLD. They apply to IPv4 and IPv6.

### Test Case # S1: SAG enabled — kernel, ASIC, IP2ME, trap, and neighbor learning

- Preconditions
  - Global SAG MAC is configured
  - SAG is enabled on the VLAN interface
  - IPv4 and/or IPv6 addresses exist on the VLAN interface

- Test steps
  1. Configure global SAG MAC and enable SAG on the VLAN interface
  2. Read the VLAN interface MAC from the kernel (`ip link show VlanX`)
  3. Read the VLAN router interface SRC MAC from ASIC_DB
  4. Confirm IPv4/IPv6 addresses exist on the VLAN interface in the kernel
  5. Confirm IPv4/IPv6 IP2ME routes exist in ASIC_DB
  6. Send packets destined to the SAG IPv4/IPv6 address and verify they are trapped to CPU (ICMP echo reply or ARP/ND reply from the DUT)
  7. From the host, ARP/ND the SAG IP and verify the learned MAC is the SAG virtual MAC
  8. Verify the switch learns the host neighbor on the VLAN interface

- Pass/Fail Criteria
  - VLAN interface kernel MAC equals the configured SAG MAC
  - VLAN RIF in ASIC_DB is programmed with the SAG MAC
  - IPv4/IPv6 addresses are present in kernel
  - IPv4/IPv6 IP2ME routes are programmed in ASIC_DB
  - Packets to the SAG IP are trapped to CPU (DUT responds)
  - Host neighbor entry for the SAG IP uses the SAG virtual MAC
  - DUT neighbor table contains the host on the VLAN interface

### Test Case # S2: SAG disabled — MAC reverts to CPU MAC

- Preconditions
  - Global SAG MAC is configured and SAG was enabled on the VLAN interface

- Test steps
  1. Disable SAG on the VLAN interface
  2. Read the VLAN interface MAC from the kernel
  3. Read the VLAN RIF SRC MAC from ASIC_DB
  4. Flush host neighbors and ARP/ND the VLAN IP

- Pass/Fail Criteria
  - VLAN interface kernel MAC equals the system CPU/router MAC
  - VLAN RIF in ASIC_DB is programmed with the CPU/router MAC
  - Host learns the CPU/router MAC, not the SAG MAC

## Dual VTEP and Asymmetric IRB Test Cases

### Test Case # 6: SAG configuration on two VTEPs

- Test steps
  1. Identify two VTEPs (two DUTs on dualtor, or DUT + PTF-simulated remote VTEP on t0)
  2. Configure the same global SAG MAC on both VTEPs
  3. Configure the same IPv4/IPv6 gateway addresses on the corresponding VLAN interfaces
  4. Enable SAG on those VLAN interfaces on both VTEPs
  5. Verify `show static-anycast-gateway` / CONFIG_DB `SAG|GLOBAL` and `VLAN_INTERFACE` `static_anycast_gateway=true`

- Pass/Fail Criteria
  - Both VTEPs report the same gateway MAC
  - Both VTEPs have SAG enabled on the same VLAN IDs
  - CONFIG_DB and APPL_DB SAG tables match the CLI

### Test Case # 7: Same SAG IP/MAC verification

- Test steps
  1. From a host on VTEP1, ARP/ND the SAG IP and record the MAC
  2. From a host on VTEP2 (or a second downlink on dualtor), ARP/ND the same SAG IP
  3. Compare SAG IPs and MACs advertised by both VTEPs
  4. Confirm SAG MAC is **not** advertised toward fabric/uplink ports

- Pass/Fail Criteria
  - Both VTEPs resolve the same SAG IP to the same SAG MAC
  - SAG MAC is learned only on host-facing VLAN members

### Test Case # 8: Host ARP → SAG

- Test steps
  1. Flush host neighbor cache
  2. Send ARP request (IPv4) or NS (IPv6) for the SAG IP from a VLAN member
  3. Capture the reply

- Pass/Fail Criteria
  - DUT replies with opcode reply / NA
  - Target hardware address is the SAG virtual MAC
  - Ethernet source of the reply is the SAG virtual MAC

### Test Case # 9: Same-VLAN L2 forwarding

- Test steps
  1. Place two hosts on the same VLAN on the same VTEP (local) and, when overlay is configured, one host on the remote VTEP in the same VLAN
  2. Send known-unicast L2 traffic (inner dest MAC = remote host MAC, dest IP in the same subnet)
  3. Confirm the packet is bridged, not routed through SAG (src MAC unchanged, no TTL decrement)

- Pass/Fail Criteria
  - Local same-VLAN unicast is forwarded to the destination VLAN member only
  - Remote same-VLAN unicast is VXLAN-encapsulated with the **L2 VNI of that VLAN** (not a routed/IRB VNI)
  - TTL of the inner packet is unchanged

### Test Case # 10: Local inter-VLAN routing

- Test steps
  1. Enable SAG on VLAN10 and VLAN20 on the same VTEP
  2. Host-A (VLAN10) uses SAG as default gateway
  3. Host-B (VLAN20) uses SAG as default gateway
  4. Send ICMP/IP from Host-A to Host-B

- Pass/Fail Criteria
  - Packet is routed (inner TTL decremented, Ethernet dest rewritten to Host-B MAC)
  - Ethernet source after routing is the SAG MAC of the egress VLAN
  - Host-A neighbor for the gateway remains the SAG MAC

### Test Case # 11: Remote inter-VLAN routing

- Test steps
  1. Host-A on VLAN10 is attached to VTEP1
  2. Host-B on VLAN20 is attached to VTEP2
  3. Program/learn overlay reachability (EVPN Type-2/5 or static equivalent)
  4. Send IP traffic Host-A → Host-B

- Pass/Fail Criteria
  - Traffic is routed on VTEP1 and encapsulated toward VTEP2
  - Host-B receives the inner packet on VLAN20

### Test Case # 12: Asymmetric IRB forward direction

- Test steps
  1. Host-A (VLAN10 @ VTEP1) sends IP to Host-B (VLAN20 @ VTEP2)
  2. Capture on the VTEP1 uplink / fabric ports

- Pass/Fail Criteria
  - Ingress leaf routes locally using SAG
  - VXLAN encapsulation uses **VNI of destination VLAN20**
  - Outer dest IP is VTEP2; inner dest MAC is Host-B (or the overlay NH MAC), not the SAG MAC of VLAN10
  - Inner TTL is decremented once at VTEP1

### Test Case # 13: Asymmetric IRB reverse direction

- Test steps
  1. Inject or originate Host-B (VLAN20 @ VTEP2) traffic to Host-A (VLAN10 @ VTEP1)
  2. On t0, PTF plays VTEP2: send VXLAN with VNI(VLAN10) after simulated remote routing, **or** send a VLAN20 packet into VTEP2 if two DUTs exist
  3. Capture on VTEP1 downlink toward Host-A

- Pass/Fail Criteria
  - Reverse path is routed on the **egress/remote** leaf (asymmetric)
  - Encapsulation toward VTEP1 uses **VNI of VLAN10** (source/destination VLAN of Host-A)
  - VTEP1 decapsulates and L2-forwards on VLAN10 to Host-A without a second routing lookup that would rewrite to a different VNI
  - Host-A receives the inner packet with dest MAC = Host-A MAC

### Test Case # 14: Verify VXLAN VNI / encapsulation

- Test steps
  1. Configure VLAN-to-VNI maps on VTEP1 (VLAN10→VNI10, VLAN20→VNI20)
  2. Send same-VLAN overlay traffic and remote inter-VLAN traffic
  3. Parse VXLAN headers on fabric ports

- Pass/Fail Criteria
  - Same-VLAN overlay packets use VNI10
  - Asymmetric IRB forward packets use VNI20
  - UDP dest port is the configured VXLAN port (default 4789)
  - Outer src IP is the local VTEP loopback; outer dest IP is the remote VTEP

### Test Case # 15: Verify VXLAN decapsulation

- Test steps
  1. From the PTF uplink, send a VXLAN packet with outer dest = DUT loopback, VNI = VLAN10 map, inner Ethernet destined to a local host on VLAN10
  2. Repeat for VNI = VLAN20 toward a local host on VLAN20

- Pass/Fail Criteria
  - Inner packet is forwarded on the matching VLAN member port
  - VXLAN header is removed
  - Packet is not flooded to unrelated VLANs

## Failure and Negative Test Cases

### Test Case # 16: Leaf failure with continuous traffic

- Test steps
  1. Start continuous Host-A ↔ Host-B traffic (local multi-homed or dualtor active-active)
  2. Fail one leaf (shutdown VLAN members / peer DUT / traffic-facing ports on one VTEP)
  3. Record loss and recovery
  4. Restore the leaf

- Pass/Fail Criteria
  - On dualtor/MCLAG-style dual attached hosts, traffic continues using the remaining leaf and the **same SAG IP/MAC** (no host gateway rewrite)
  - On single-DUT, traffic using a remaining VLAN member continues; traffic pinned to the failed member stops until restored
  - After restore, both paths/leaves forward again

### Test Case # 17: EVPN/BGP failure

- Test steps
  1. Establish overlay forwarding (remote inter-VLAN or same-VLAN VXLAN)
  2. Shut down BGP (all neighbors or EVPN address-family as supported)
  3. Send overlay traffic
  4. Restore BGP and wait for session/route re-establishment
  5. Resend overlay traffic

- Pass/Fail Criteria
  - While BGP/EVPN is down, remote overlay forwarding fails (no encap to withdrawn VTEP, or underlay to remote VTEP is missing)
  - Local SAG ARP and local inter-VLAN routing **remain** functional
  - After BGP recovers, remote overlay forwarding resumes

### Test Case # 18: MAC mobility

- Test steps
  1. Learn Host-A MAC on VTEP1 port P1 (and advertise/learn in overlay if remote)
  2. Reattach the same MAC on port P2 of the same VTEP, or on VTEP2
  3. Send unicast to that MAC

- Pass/Fail Criteria
  - FDB (and EVPN Type-2 if present) updates to the new location
  - Subsequent unicast is forwarded to the new port/VTEP only
  - SAG IP/MAC binding is unchanged

### Test Case # 19: VLAN/VNI mismatch negative test

- Test steps
  1. Configure VLAN10 → VNI10 on VTEP1
  2. Send a VXLAN packet to the DUT with a VNI that is not mapped (or mapped to a different VLAN than the inner 802.1Q/payload VLAN)
  3. Optionally map VNI20 on VTEP2 only, leaving VTEP1 without VNI20

- Pass/Fail Criteria
  - Unmapped VNI packets are dropped (not decapped onto an arbitrary VLAN)
  - Inner payload does not appear on the wrong VLAN member ports

### Test Case # 20: Missing EVPN route / unreachable VTEP negative test

- Test steps
  1. With overlay maps present, **do not** install (or withdraw) the remote host/VTEP route
  2. Send Host-A traffic to the remote Host-B IP
  3. Make the remote VTEP underlay prefix unreachable (BGP shutdown or remove the route to the VTEP loopback)
  4. Restore routes and retest

- Pass/Fail Criteria
  - Without an overlay route, traffic to Host-B is not VXLAN-encapsulated toward a random VTEP (dropped or locally handled, never blackholed with a wrong VNI)
  - If the remote VTEP is unreachable, encapsulated packets are not successfully delivered
  - After routes return, forwarding succeeds again
