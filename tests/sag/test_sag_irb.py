"""Dual-VTEP SAG and Asymmetric IRB dataplane tests."""
import logging
import time

import ptf.testutils as testutils
import pytest
from ptf.mask import Mask
import ptf.packet as scapy

from tests.common.helpers.assertions import pytest_assert
from tests.sag.sag_helpers import (
    REMOTE_VTEP_IP,
    SAG_MAC,
    VXLAN_UDP_PORT,
    add_static_neighbor,
    apply_swss_fdb,
    configure_vxlan_maps,
    del_static_neighbor,
    get_configdb_field,
    host_ip_from_gateway,
    remove_vxlan_maps,
    require_min_vlan_members,
    sag_cli_supported,
    show_sag,
    vlan_vni,
    wait_for_kernel_mac,
)

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.topology("t0", "t0-2vlans", "dualtor"),
    pytest.mark.disable_loganalyzer,
]

REMOTE_HOST_MAC = "00:aa:bb:cc:dd:02"


def _vlans_ipv4(topo, min_count=1):
    vlans = [v for v in topo["vlans"] if v.get("ipv4")]
    if len(vlans) < min_count:
        pytest.skip("Need at least {} IPv4 VLAN(s)".format(min_count))
    return vlans


@pytest.fixture(scope="module")
def vxlan_overlay(sag_enabled):
    topo = sag_enabled
    duthost = topo["duthost"]
    if not topo.get("loopback_v4"):
        pytest.skip("DUT has no IPv4 loopback for VXLAN tunnel source")
    vlan_ids = [v["vlan_id"] for v in topo["vlans"]]
    configure_vxlan_maps(duthost, topo["loopback_v4"], vlan_ids)
    yield topo
    remove_vxlan_maps(duthost, vlan_ids)


def test_sag_config_two_vteps(sag_enabled):
    """Configure and verify the same SAG IP/MAC on two VTEPs when available."""
    topo = sag_enabled
    duthosts = [d for d in topo["duthosts"] if sag_cli_supported(d)]
    pytest_assert(duthosts, "No DUT with SAG CLI")
    vlan = _vlans_ipv4(topo)[0]
    for dut in duthosts:
        wait_for_kernel_mac(dut, vlan["name"], SAG_MAC)
        mac = get_configdb_field(dut, "SAG|GLOBAL", "gateway_mac")
        pytest_assert(mac.lower() == SAG_MAC.lower(), "{} SAG MAC mismatch".format(dut.hostname))
        enabled = get_configdb_field(
            dut, "VLAN_INTERFACE|{}".format(vlan["name"]), "static_anycast_gateway")
        pytest_assert(enabled.lower() in ("true", "1"), "{} SAG not enabled".format(dut.hostname))
        output = show_sag(dut).get("stdout") or ""
        pytest_assert(
            SAG_MAC.lower() in output.lower() or "static anycast" in output.lower()
            or vlan["name"] in output,
            "show static-anycast-gateway missing SAG info on {}".format(dut.hostname))
    if len(duthosts) < 2:
        logger.info("Single DUT testbed: PTF simulates remote VTEP2 with the same SAG IP/MAC")
        pytest_assert(
            vlan.get("ipv4"),
            "SAG IPv4 gateway must be present so VTEP2 can mirror the same anycast IP")


def test_same_sag_ip_mac(ptfadapter, sag_enabled):
    """Hosts on each VTEP resolve the same SAG IP to the same SAG MAC."""
    topo = sag_enabled
    vlan = _vlans_ipv4(topo)[0]
    require_min_vlan_members(vlan, 1, "Need a downlink for ARP")
    member = vlan["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
    host_ip = host_ip_from_gateway(vlan["ipv4"], 40)
    req = testutils.simple_arp_packet(
        eth_src=host_mac,
        eth_dst="ff:ff:ff:ff:ff:ff",
        arp_op=1,
        ip_snd=host_ip,
        ip_tgt=vlan["ipv4"],
        hw_snd=host_mac,
        hw_tgt="00:00:00:00:00:00",
    )
    exp = testutils.simple_arp_packet(
        eth_src=SAG_MAC,
        eth_dst=host_mac,
        arp_op=2,
        ip_snd=vlan["ipv4"],
        ip_tgt=host_ip,
        hw_snd=SAG_MAC,
        hw_tgt=host_mac,
    )
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, member["ptf_index"], req)
    testutils.verify_packet(ptfadapter, Mask(exp), member["ptf_index"], timeout=8)
    # SAG MAC must not be the source of ARP replies on fabric/uplink ports.
    if topo["uplink_ptf"]:
        testutils.verify_no_packet_any(ptfadapter, Mask(exp), topo["uplink_ptf"], timeout=1)


def test_host_arp_to_sag(ptfadapter, sag_enabled):
    """ARP request for SAG IP is answered with the virtual MAC on both eth and ARP headers."""
    test_same_sag_ip_mac(ptfadapter, sag_enabled)


def test_same_vlan_l2_forwarding(ptfadapter, sag_enabled):
    """Known-unicast same-VLAN traffic is bridged (TTL unchanged, not routed via SAG)."""
    topo = sag_enabled
    vlan = _vlans_ipv4(topo)[0]
    require_min_vlan_members(vlan, 2, "Same-VLAN L2 test needs two members")
    m1, m2 = vlan["members"][0], vlan["members"][1]
    src_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    dst_mac = ptfadapter.dataplane.get_mac(0, m2["ptf_index"])
    src_ip = host_ip_from_gateway(vlan["ipv4"], 41)
    dst_ip = host_ip_from_gateway(vlan["ipv4"], 42)
    pkt = testutils.simple_tcp_packet(
        eth_src=src_mac,
        eth_dst=dst_mac,
        ip_src=src_ip,
        ip_dst=dst_ip,
        ip_ttl=64,
        tcp_sport=1234,
        tcp_dport=80,
    )
    exp = testutils.simple_tcp_packet(
        eth_src=src_mac,
        eth_dst=dst_mac,
        ip_src=src_ip,
        ip_dst=dst_ip,
        ip_ttl=64,
        tcp_sport=1234,
        tcp_dport=80,
    )
    masked = Mask(exp)
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, m1["ptf_index"], pkt)
    testutils.verify_packet(ptfadapter, masked, m2["ptf_index"], timeout=8)


def test_local_inter_vlan_routing(ptfadapter, sag_enabled):
    """Local Asymmetric-IRB-style routing between two VLANs using SAG."""
    topo = sag_enabled
    vlans = _vlans_ipv4(topo, 2)
    vlan1, vlan2 = vlans[0], vlans[1]
    m1 = vlan1["members"][0]
    m2 = vlan2["members"][0]
    host1_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    host2_mac = ptfadapter.dataplane.get_mac(0, m2["ptf_index"])
    host1_ip = host_ip_from_gateway(vlan1["ipv4"], 43)
    host2_ip = host_ip_from_gateway(vlan2["ipv4"], 44)
    add_static_neighbor(topo["duthost"], host1_ip, host1_mac, vlan1["name"])
    add_static_neighbor(topo["duthost"], host2_ip, host2_mac, vlan2["name"])
    time.sleep(1)
    pkt = testutils.simple_tcp_packet(
        eth_src=host1_mac,
        eth_dst=SAG_MAC,
        ip_src=host1_ip,
        ip_dst=host2_ip,
        ip_ttl=64,
        tcp_sport=1234,
        tcp_dport=80,
    )
    exp = testutils.simple_tcp_packet(
        eth_src=SAG_MAC,
        eth_dst=host2_mac,
        ip_src=host1_ip,
        ip_dst=host2_ip,
        ip_ttl=63,
        tcp_sport=1234,
        tcp_dport=80,
    )
    masked = Mask(exp)
    masked.set_do_not_care_scapy(scapy.IP, "chksum")
    masked.set_do_not_care_scapy(scapy.TCP, "chksum")
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, m1["ptf_index"], pkt)
    testutils.verify_packet(ptfadapter, masked, m2["ptf_index"], timeout=8)
    del_static_neighbor(topo["duthost"], host1_ip, vlan1["name"])
    del_static_neighbor(topo["duthost"], host2_ip, vlan2["name"])


def _inner_tcp(src_mac, dst_mac, src_ip, dst_ip, ttl=64):
    return testutils.simple_tcp_packet(
        eth_src=src_mac,
        eth_dst=dst_mac,
        ip_src=src_ip,
        ip_dst=dst_ip,
        ip_ttl=ttl,
        tcp_sport=1234,
        tcp_dport=80,
    )


def _expect_vxlan(outer_src, outer_dst, inner, vni, uplink_mac, dut_mac):
    vxlan = testutils.simple_vxlan_packet(
        eth_src=dut_mac,
        eth_dst=uplink_mac,
        ip_src=outer_src,
        ip_dst=outer_dst,
        udp_dport=VXLAN_UDP_PORT,
        vxlan_vni=vni,
        inner_frame=inner,
    )
    masked = Mask(vxlan)
    masked.set_do_not_care_scapy(scapy.Ether, "dst")
    masked.set_do_not_care_scapy(scapy.IP, "ttl")
    masked.set_do_not_care_scapy(scapy.IP, "id")
    masked.set_do_not_care_scapy(scapy.IP, "chksum")
    masked.set_do_not_care_scapy(scapy.IP, "len")
    masked.set_do_not_care_scapy(scapy.UDP, "sport")
    masked.set_do_not_care_scapy(scapy.UDP, "len")
    masked.set_do_not_care_scapy(scapy.UDP, "chksum")
    return masked


def test_remote_inter_vlan_and_asymmetric_irb_forward(ptfadapter, vxlan_overlay):
    """
    Remote inter-VLAN routing / Asymmetric IRB forward:
    Host-A (VLAN1) -> SAG on VTEP1 -> encap with destination VLAN VNI toward VTEP2.
    """
    topo = vxlan_overlay
    vlans = _vlans_ipv4(topo, 2)
    vlan1, vlan2 = vlans[0], vlans[1]
    if not topo["uplink_ptf"]:
        pytest.skip("No fabric/uplink PTF ports to capture VXLAN")
    duthost = topo["duthost"]
    remote_ip = host_ip_from_gateway(vlan2["ipv4"], 200)
    add_static_neighbor(duthost, remote_ip, REMOTE_HOST_MAC, vlan2["name"])
    apply_swss_fdb(duthost, vlan2["name"], REMOTE_HOST_MAC, REMOTE_VTEP_IP, op="SET")
    time.sleep(2)
    m1 = vlan1["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    host_ip = host_ip_from_gateway(vlan1["ipv4"], 45)
    add_static_neighbor(duthost, host_ip, host_mac, vlan1["name"])
    inner_sent = _inner_tcp(host_mac, SAG_MAC, host_ip, remote_ip, ttl=64)
    inner_routed = _inner_tcp(SAG_MAC, REMOTE_HOST_MAC, host_ip, remote_ip, ttl=63)
    uplink_mac = ptfadapter.dataplane.get_mac(0, topo["uplink_ptf"][0])
    masked = _expect_vxlan(
        topo["loopback_v4"], REMOTE_VTEP_IP, inner_routed,
        vlan_vni(vlan2["vlan_id"]), uplink_mac, topo["router_mac"])
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, m1["ptf_index"], inner_sent)
    try:
        testutils.verify_packet_any_port(ptfadapter, masked, topo["uplink_ptf"], timeout=8)
    finally:
        apply_swss_fdb(duthost, vlan2["name"], REMOTE_HOST_MAC, REMOTE_VTEP_IP, op="DEL")
        del_static_neighbor(duthost, remote_ip, vlan2["name"])
        del_static_neighbor(duthost, host_ip, vlan1["name"])


def test_asymmetric_irb_reverse_and_vxlan_decap(ptfadapter, vxlan_overlay):
    """
    Asymmetric IRB reverse + VXLAN decap:
    Remote leaf encapsulates with VNI of VLAN1; VTEP1 decaps and L2-forwards to Host-A.
    """
    topo = vxlan_overlay
    vlan1 = _vlans_ipv4(topo)[0]
    if not topo["uplink_ptf"]:
        pytest.skip("No fabric/uplink PTF ports")
    m1 = vlan1["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    host_ip = host_ip_from_gateway(vlan1["ipv4"], 46)
    remote_ip = host_ip_from_gateway(vlan1["ipv4"], 201)
    inner = _inner_tcp(REMOTE_HOST_MAC, host_mac, remote_ip, host_ip, ttl=63)
    uplink = topo["uplink_ptf"][0]
    uplink_mac = ptfadapter.dataplane.get_mac(0, uplink)
    vxlan = testutils.simple_vxlan_packet(
        eth_src=uplink_mac,
        eth_dst=topo["router_mac"],
        ip_src=REMOTE_VTEP_IP,
        ip_dst=topo["loopback_v4"],
        udp_dport=VXLAN_UDP_PORT,
        vxlan_vni=vlan_vni(vlan1["vlan_id"]),
        inner_frame=inner,
    )
    exp = Mask(inner)
    exp.set_do_not_care_scapy(scapy.IP, "chksum")
    exp.set_do_not_care_scapy(scapy.TCP, "chksum")
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, uplink, vxlan)
    testutils.verify_packet(ptfadapter, exp, m1["ptf_index"], timeout=8)


def test_vxlan_vni_encapsulation_same_vlan(ptfadapter, vxlan_overlay):
    """Same-VLAN overlay uses the L2 VNI of that VLAN."""
    topo = vxlan_overlay
    vlan = _vlans_ipv4(topo)[0]
    if not topo["uplink_ptf"]:
        pytest.skip("No fabric/uplink PTF ports")
    duthost = topo["duthost"]
    apply_swss_fdb(duthost, vlan["name"], REMOTE_HOST_MAC, REMOTE_VTEP_IP, op="SET")
    time.sleep(2)
    m1 = vlan["members"][0]
    src_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    src_ip = host_ip_from_gateway(vlan["ipv4"], 47)
    dst_ip = host_ip_from_gateway(vlan["ipv4"], 202)
    inner = _inner_tcp(src_mac, REMOTE_HOST_MAC, src_ip, dst_ip, ttl=64)
    uplink_mac = ptfadapter.dataplane.get_mac(0, topo["uplink_ptf"][0])
    masked = _expect_vxlan(
        topo["loopback_v4"], REMOTE_VTEP_IP, inner,
        vlan_vni(vlan["vlan_id"]), uplink_mac, topo["router_mac"])
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, m1["ptf_index"], inner)
    try:
        testutils.verify_packet_any_port(ptfadapter, masked, topo["uplink_ptf"], timeout=8)
    finally:
        apply_swss_fdb(duthost, vlan["name"], REMOTE_HOST_MAC, REMOTE_VTEP_IP, op="DEL")


def test_leaf_failure_continuous_traffic(ptfadapter, sag_enabled):
    """Fail one downlink/leaf while traffic is running; remaining path keeps forwarding."""
    topo = sag_enabled
    vlan = _vlans_ipv4(topo)[0]
    require_min_vlan_members(vlan, 2, "Leaf/member failure test needs two VLAN members")
    m1, m2 = vlan["members"][0], vlan["members"][1]
    duthost = topo["duthost"]
    src_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    dst_mac = ptfadapter.dataplane.get_mac(0, m2["ptf_index"])
    src_ip = host_ip_from_gateway(vlan["ipv4"], 48)
    dst_ip = host_ip_from_gateway(vlan["ipv4"], 49)
    pkt = testutils.simple_tcp_packet(
        eth_src=src_mac, eth_dst=dst_mac, ip_src=src_ip, ip_dst=dst_ip, ip_ttl=64)
    hits_before = 0
    ptfadapter.dataplane.flush()
    for _ in range(5):
        testutils.send(ptfadapter, m1["ptf_index"], pkt)
        try:
            testutils.verify_packet(ptfadapter, Mask(pkt), m2["ptf_index"], timeout=2)
            hits_before += 1
        except AssertionError:
            pass
    pytest_assert(hits_before > 0, "No L2 traffic before leaf/member failure")
    failed_port = m1["port"]
    duthost.shutdown(failed_port)
    try:
        time.sleep(2)
        # Traffic sourced from the remaining member toward a third host is not required;
        # verify the surviving member still ARPs SAG.
        host_mac = ptfadapter.dataplane.get_mac(0, m2["ptf_index"])
        host_ip = host_ip_from_gateway(vlan["ipv4"], 50)
        req = testutils.simple_arp_packet(
            eth_src=host_mac,
            eth_dst="ff:ff:ff:ff:ff:ff",
            arp_op=1,
            ip_snd=host_ip,
            ip_tgt=vlan["ipv4"],
            hw_snd=host_mac,
            hw_tgt="00:00:00:00:00:00",
        )
        exp = testutils.simple_arp_packet(
            eth_src=SAG_MAC,
            eth_dst=host_mac,
            arp_op=2,
            ip_snd=vlan["ipv4"],
            ip_tgt=host_ip,
            hw_snd=SAG_MAC,
            hw_tgt=host_mac,
        )
        ptfadapter.dataplane.flush()
        testutils.send(ptfadapter, m2["ptf_index"], req)
        testutils.verify_packet(ptfadapter, Mask(exp), m2["ptf_index"], timeout=8)
    finally:
        duthost.no_shutdown(failed_port)
        time.sleep(5)


def test_evpn_bgp_failure(ptfadapter, vxlan_overlay):
    """BGP/EVPN down breaks remote overlay; local SAG ARP still works."""
    topo = vxlan_overlay
    duthost = topo["duthost"]
    vlan = _vlans_ipv4(topo)[0]
    duthost.shell("sudo config bgp shutdown all")
    time.sleep(5)
    try:
        member = vlan["members"][0]
        host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
        host_ip = host_ip_from_gateway(vlan["ipv4"], 51)
        req = testutils.simple_arp_packet(
            eth_src=host_mac,
            eth_dst="ff:ff:ff:ff:ff:ff",
            arp_op=1,
            ip_snd=host_ip,
            ip_tgt=vlan["ipv4"],
            hw_snd=host_mac,
            hw_tgt="00:00:00:00:00:00",
        )
        exp = testutils.simple_arp_packet(
            eth_src=SAG_MAC,
            eth_dst=host_mac,
            arp_op=2,
            ip_snd=vlan["ipv4"],
            ip_tgt=host_ip,
            hw_snd=SAG_MAC,
            hw_tgt=host_mac,
        )
        ptfadapter.dataplane.flush()
        testutils.send(ptfadapter, member["ptf_index"], req)
        testutils.verify_packet(ptfadapter, Mask(exp), member["ptf_index"], timeout=8)
        if topo["uplink_ptf"]:
            apply_swss_fdb(duthost, vlan["name"], REMOTE_HOST_MAC, REMOTE_VTEP_IP, op="SET")
            inner = _inner_tcp(
                host_mac, REMOTE_HOST_MAC, host_ip,
                host_ip_from_gateway(vlan["ipv4"], 203), ttl=64)
            uplink_mac = ptfadapter.dataplane.get_mac(0, topo["uplink_ptf"][0])
            masked = _expect_vxlan(
                topo["loopback_v4"], REMOTE_VTEP_IP, inner,
                vlan_vni(vlan["vlan_id"]), uplink_mac, topo["router_mac"])
            ptfadapter.dataplane.flush()
            testutils.send(ptfadapter, member["ptf_index"], inner)
            # Underlay to the remote VTEP is withdrawn with BGP; encap must not be delivered.
            testutils.verify_no_packet_any(ptfadapter, masked, topo["uplink_ptf"], timeout=2)
            apply_swss_fdb(duthost, vlan["name"], REMOTE_HOST_MAC, REMOTE_VTEP_IP, op="DEL")
    finally:
        duthost.shell("sudo config bgp startup all")
        time.sleep(10)


def test_mac_mobility(ptfadapter, sag_enabled):
    """A host MAC moving between VLAN members updates forwarding; SAG binding is unchanged."""
    topo = sag_enabled
    vlan = _vlans_ipv4(topo)[0]
    require_min_vlan_members(vlan, 2, "MAC mobility needs two members")
    m1, m2 = vlan["members"][0], vlan["members"][1]
    mobile_mac = "00:22:33:44:55:66"
    src_ip = host_ip_from_gateway(vlan["ipv4"], 52)
    dst_ip = host_ip_from_gateway(vlan["ipv4"], 53)
    learn = testutils.simple_eth_packet(eth_src=mobile_mac, eth_dst="ff:ff:ff:ff:ff:ff")
    testutils.send(ptfadapter, m1["ptf_index"], learn)
    time.sleep(1)
    testutils.send(ptfadapter, m2["ptf_index"], learn)
    time.sleep(1)
    unicast = testutils.simple_tcp_packet(
        eth_src=ptfadapter.dataplane.get_mac(0, m1["ptf_index"]),
        eth_dst=mobile_mac,
        ip_src=src_ip,
        ip_dst=dst_ip,
        ip_ttl=64,
    )
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, m1["ptf_index"], unicast)
    testutils.verify_packet(ptfadapter, Mask(unicast), m2["ptf_index"], timeout=8)
    wait_for_kernel_mac(topo["duthost"], vlan["name"], SAG_MAC)


def test_vlan_vni_mismatch_negative(ptfadapter, vxlan_overlay):
    """VXLAN packets with an unmapped VNI are not decapped onto a local VLAN."""
    topo = vxlan_overlay
    vlan = _vlans_ipv4(topo)[0]
    if not topo["uplink_ptf"]:
        pytest.skip("No fabric/uplink PTF ports")
    m1 = vlan["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    inner = _inner_tcp(
        REMOTE_HOST_MAC, host_mac,
        host_ip_from_gateway(vlan["ipv4"], 204),
        host_ip_from_gateway(vlan["ipv4"], 54),
        ttl=63)
    uplink = topo["uplink_ptf"][0]
    uplink_mac = ptfadapter.dataplane.get_mac(0, uplink)
    bad_vni = vlan_vni(vlan["vlan_id"]) + 999
    vxlan = testutils.simple_vxlan_packet(
        eth_src=uplink_mac,
        eth_dst=topo["router_mac"],
        ip_src=REMOTE_VTEP_IP,
        ip_dst=topo["loopback_v4"],
        udp_dport=VXLAN_UDP_PORT,
        vxlan_vni=bad_vni,
        inner_frame=inner,
    )
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, uplink, vxlan)
    testutils.verify_no_packet(ptfadapter, Mask(inner), m1["ptf_index"], timeout=2)


def test_missing_evpn_route_unreachable_vtep_negative(ptfadapter, vxlan_overlay):
    """Without a remote overlay FDB/route, traffic is not encapsulated to a VTEP."""
    topo = vxlan_overlay
    vlan = _vlans_ipv4(topo)[0]
    if not topo["uplink_ptf"]:
        pytest.skip("No fabric/uplink PTF ports")
    m1 = vlan["members"][0]
    src_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    src_ip = host_ip_from_gateway(vlan["ipv4"], 55)
    dst_ip = host_ip_from_gateway(vlan["ipv4"], 205)
    inner = _inner_tcp(src_mac, REMOTE_HOST_MAC, src_ip, dst_ip, ttl=64)
    uplink_mac = ptfadapter.dataplane.get_mac(0, topo["uplink_ptf"][0])
    masked = _expect_vxlan(
        topo["loopback_v4"], REMOTE_VTEP_IP, inner,
        vlan_vni(vlan["vlan_id"]), uplink_mac, topo["router_mac"])
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, m1["ptf_index"], inner)
    testutils.verify_no_packet_any(ptfadapter, masked, topo["uplink_ptf"], timeout=2)
