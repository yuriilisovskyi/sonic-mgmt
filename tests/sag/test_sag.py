"""Baseline SAG functional tests (IPv4/IPv6 ping, routing, MAC change, enable, reload)."""
import time

import ptf.testutils as testutils
import pytest
from ptf.mask import Mask
import ptf.packet as scapy

from tests.common.config_reload import config_reload
from tests.common.helpers.assertions import pytest_assert
from tests.sag.sag_helpers import (
    SAG_MAC,
    SAG_MAC_ALT,
    add_sag_mac,
    add_static_neighbor,
    del_sag_mac,
    get_kernel_iface_mac,
    host_ip_from_gateway,
    require_min_vlan_members,
    set_sag_enabled,
    wait_for_kernel_mac,
)

pytestmark = [
    pytest.mark.topology("t0", "t0-2vlans", "ptf", "dualtor"),
]


def _send_icmp(ptfadapter, src_port, dst_ports, src_mac, dst_mac, src_ip, dst_ip, vlan_id=0, expect=True):
    pkt = testutils.simple_icmp_packet(
        eth_src=src_mac,
        eth_dst=dst_mac,
        ip_src=src_ip,
        ip_dst=dst_ip,
        icmp_type=8,
        icmp_code=0,
        pktlen=100 if not vlan_id else 104,
        dl_vlan_enable=bool(vlan_id),
        vlan_vid=vlan_id,
    )
    exp = testutils.simple_icmp_packet(
        eth_src=dst_mac,
        eth_dst=src_mac,
        ip_src=src_ip,
        ip_dst=dst_ip,
        icmp_type=8,
        pktlen=100 if not vlan_id else 104,
        dl_vlan_enable=bool(vlan_id),
        vlan_vid=vlan_id,
    )
    masked = Mask(exp)
    masked.set_do_not_care_scapy(scapy.Ether, "src")
    masked.set_do_not_care_scapy(scapy.Ether, "dst")
    masked.set_do_not_care_scapy(scapy.IP, "ttl")
    masked.set_do_not_care_scapy(scapy.IP, "chksum")
    masked.set_do_not_care_scapy(scapy.ICMP, "chksum")
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, src_port, pkt)
    if expect:
        testutils.verify_packet_any_port(ptfadapter, masked, dst_ports, timeout=5)
    else:
        testutils.verify_no_packet_any(ptfadapter, masked, dst_ports, timeout=2)


def _first_ipv4_vlan(sag_topo):
    for vlan in sag_topo["vlans"]:
        if vlan.get("ipv4"):
            return vlan
    pytest.skip("No IPv4 VLAN interface available")


def _two_ipv4_vlans(sag_topo):
    vlans = [v for v in sag_topo["vlans"] if v.get("ipv4")]
    pytest_assert(
        len(vlans) >= 2,
        "Failed to provide two IPv4 VLANs for inter-VLAN SAG routing (found {})".format(len(vlans)))
    return vlans[0], vlans[1]


def test_sag_ping(ptfadapter, sag_enabled):
    """Host ARP/ICMP to SAG IPv4 address learns the SAG MAC."""
    topo = sag_enabled
    vlan = _first_ipv4_vlan(topo)
    member = vlan["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
    host_ip = host_ip_from_gateway(vlan["ipv4"], 10)
    wait_for_kernel_mac(topo["duthost"], vlan["name"], SAG_MAC)
    ptfadapter.dataplane.flush()
    pkt = testutils.simple_arp_packet(
        eth_src=host_mac,
        eth_dst="ff:ff:ff:ff:ff:ff",
        arp_op=1,
        ip_snd=host_ip,
        ip_tgt=vlan["ipv4"],
        hw_snd=host_mac,
        hw_tgt="00:00:00:00:00:00",
    )
    testutils.send(ptfadapter, member["ptf_index"], pkt)
    exp = testutils.simple_arp_packet(
        eth_src=SAG_MAC,
        eth_dst=host_mac,
        arp_op=2,
        ip_snd=vlan["ipv4"],
        ip_tgt=host_ip,
        hw_snd=SAG_MAC,
        hw_tgt=host_mac,
    )
    masked = Mask(exp)
    masked.set_do_not_care_scapy(scapy.Ether, "type")
    testutils.verify_packet(ptfadapter, masked, member["ptf_index"], timeout=8)


def test_sag_route(ptfadapter, sag_enabled):
    """Inter-VLAN routing over SAG. A second VLAN is created when the DUT has only one."""
    topo = sag_enabled
    vlan1, vlan2 = _two_ipv4_vlans(topo)
    require_min_vlan_members(vlan1, 1, "VLAN1 needs a member port")
    require_min_vlan_members(vlan2, 1, "VLAN2 needs a member port")
    m1 = vlan1["members"][0]
    m2 = vlan2["members"][0]
    host1_mac = ptfadapter.dataplane.get_mac(0, m1["ptf_index"])
    host2_mac = ptfadapter.dataplane.get_mac(0, m2["ptf_index"])
    host1_ip = host_ip_from_gateway(vlan1["ipv4"], 10)
    host2_ip = host_ip_from_gateway(vlan2["ipv4"], 20)
    add_static_neighbor(topo["duthost"], host2_ip, host2_mac, vlan2["name"])
    add_static_neighbor(topo["duthost"], host1_ip, host1_mac, vlan1["name"])
    time.sleep(1)
    _send_icmp(
        ptfadapter,
        m1["ptf_index"],
        [m2["ptf_index"]],
        host1_mac,
        SAG_MAC,
        host1_ip,
        host2_ip,
        expect=True,
    )


def test_sag_change_enable_field(ptfadapter, sag_enabled):
    """Disabling SAG stops advertising the SAG MAC; re-enable restores it."""
    topo = sag_enabled
    duthost = topo["duthost"]
    vlan = _first_ipv4_vlan(topo)
    member = vlan["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
    host_ip = host_ip_from_gateway(vlan["ipv4"], 11)
    set_sag_enabled(duthost, vlan["vlan_id"], False)
    wait_for_kernel_mac(duthost, vlan["name"], topo["router_mac"])
    pytest_assert(
        get_kernel_iface_mac(duthost, vlan["name"]) != SAG_MAC.lower(),
        "SAG MAC still present on kernel interface after disable")
    pkt = testutils.simple_arp_packet(
        eth_src=host_mac,
        eth_dst="ff:ff:ff:ff:ff:ff",
        arp_op=1,
        ip_snd=host_ip,
        ip_tgt=vlan["ipv4"],
        hw_snd=host_mac,
        hw_tgt="00:00:00:00:00:00",
    )
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, member["ptf_index"], pkt)
    exp_sag = testutils.simple_arp_packet(
        eth_src=SAG_MAC,
        eth_dst=host_mac,
        arp_op=2,
        ip_snd=vlan["ipv4"],
        ip_tgt=host_ip,
        hw_snd=SAG_MAC,
        hw_tgt=host_mac,
    )
    masked = Mask(exp_sag)
    testutils.verify_no_packet(ptfadapter, masked, member["ptf_index"], timeout=2)
    set_sag_enabled(duthost, vlan["vlan_id"], True)
    wait_for_kernel_mac(duthost, vlan["name"], SAG_MAC)
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, member["ptf_index"], pkt)
    testutils.verify_packet(ptfadapter, masked, member["ptf_index"], timeout=8)


def test_sag_change_sag_mac(ptfadapter, sag_enabled):
    """Changing the global SAG MAC is reflected in ARP replies."""
    topo = sag_enabled
    duthost = topo["duthost"]
    vlan = _first_ipv4_vlan(topo)
    member = vlan["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
    host_ip = host_ip_from_gateway(vlan["ipv4"], 12)
    wait_for_kernel_mac(duthost, vlan["name"], SAG_MAC)
    del_sag_mac(duthost, SAG_MAC)
    add_sag_mac(duthost, SAG_MAC_ALT)
    wait_for_kernel_mac(duthost, vlan["name"], SAG_MAC_ALT)
    pkt = testutils.simple_arp_packet(
        eth_src=host_mac,
        eth_dst="ff:ff:ff:ff:ff:ff",
        arp_op=1,
        ip_snd=host_ip,
        ip_tgt=vlan["ipv4"],
        hw_snd=host_mac,
        hw_tgt="00:00:00:00:00:00",
    )
    exp = testutils.simple_arp_packet(
        eth_src=SAG_MAC_ALT,
        eth_dst=host_mac,
        arp_op=2,
        ip_snd=vlan["ipv4"],
        ip_tgt=host_ip,
        hw_snd=SAG_MAC_ALT,
        hw_tgt=host_mac,
    )
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, member["ptf_index"], pkt)
    testutils.verify_packet(ptfadapter, Mask(exp), member["ptf_index"], timeout=8)
    del_sag_mac(duthost, SAG_MAC_ALT)
    add_sag_mac(duthost, SAG_MAC)
    wait_for_kernel_mac(duthost, vlan["name"], SAG_MAC)


def test_sag_ping_after_reload(ptfadapter, sag_enabled):
    """SAG MAC survives config reload from config_db."""
    topo = sag_enabled
    duthost = topo["duthost"]
    duthost.shell("config save -y")
    config_reload(duthost, config_source="config_db", safe_reload=True, wait_for_bgp=True)
    vlan = _first_ipv4_vlan(topo)
    wait_for_kernel_mac(duthost, vlan["name"], SAG_MAC, timeout=60)
    member = vlan["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
    host_ip = host_ip_from_gateway(vlan["ipv4"], 13)
    pkt = testutils.simple_arp_packet(
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
    testutils.send(ptfadapter, member["ptf_index"], pkt)
    testutils.verify_packet(ptfadapter, Mask(exp), member["ptf_index"], timeout=8)


def test_sag_ping_ipv6(ptfadapter, sag_enabled):
    """IPv6 echo to the SAG address is trapped to CPU and answered with the SAG MAC."""
    topo = sag_enabled
    vlan = None
    for candidate in topo["vlans"]:
        if candidate.get("ipv6"):
            vlan = candidate
            break
    if vlan is None:
        pytest.skip("No IPv6 VLAN interface available")
    member = vlan["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
    gw_ip = vlan["ipv6"].split("/")[0]
    host_ip = host_ip_from_gateway(gw_ip, 10)
    wait_for_kernel_mac(topo["duthost"], vlan["name"], SAG_MAC)
    pkt = testutils.simple_icmpv6_packet(
        eth_src=host_mac,
        eth_dst=SAG_MAC,
        ipv6_src=host_ip,
        ipv6_dst=gw_ip,
        icmp_type=128,
        icmp_code=0,
    )
    exp = testutils.simple_icmpv6_packet(
        eth_src=SAG_MAC,
        eth_dst=host_mac,
        ipv6_src=gw_ip,
        ipv6_dst=host_ip,
        icmp_type=129,
        icmp_code=0,
    )
    masked = Mask(exp)
    masked.set_do_not_care_scapy(scapy.IPv6, "fl")
    masked.set_do_not_care_scapy(scapy.IPv6, "tc")
    masked.set_do_not_care_scapy(scapy.IPv6, "hlim")
    masked.set_do_not_care_scapy(scapy.IPv6, "plen")
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, member["ptf_index"], pkt)
    testutils.verify_packet(ptfadapter, masked, member["ptf_index"], timeout=8)
