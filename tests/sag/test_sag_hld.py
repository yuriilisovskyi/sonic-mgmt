"""System test cases from the SAG HLD."""
import logging

import ptf.testutils as testutils
import pytest
from ptf.mask import Mask
import ptf.packet as scapy

from tests.common.helpers.assertions import pytest_assert
from tests.common.utilities import wait_until
from tests.sag.sag_helpers import (
    SAG_MAC,
    dut_neighbor_mac,
    get_configdb_field,
    get_kernel_iface_mac,
    get_vlan_rif_mac_from_asic,
    host_ip_from_gateway,
    ip2me_route_programmed,
    kernel_addr_present,
    set_sag_enabled,
    wait_for_kernel_mac,
)

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.topology("t0", "t0-2vlans", "ptf", "dualtor"),
]


def _ipv4_vlan(sag_topo):
    for vlan in sag_topo["vlans"]:
        if vlan.get("ipv4"):
            return vlan
    pytest.skip("No IPv4 VLAN interface available")


def test_sag_hld_enabled_kernel_asic_ip2me_trap_neighbor(ptfadapter, sag_enabled):
    """
    HLD system case 1: SAG enabled on VLAN.
    Verify kernel MAC, ASIC RIF MAC, IP addresses, IP2ME, CPU trap, host SAG MAC, DUT neighbor.
    """
    topo = sag_enabled
    duthost = topo["duthost"]
    vlan = _ipv4_vlan(topo)
    logger.info("HLD case 1: SAG enabled checks on %s", vlan["name"])
    wait_for_kernel_mac(duthost, vlan["name"], SAG_MAC)
    pytest_assert(
        get_kernel_iface_mac(duthost, vlan["name"]) == SAG_MAC.lower(),
        "VLAN interface was not created/updated with SAG MAC in kernel")

    cfg_mac = get_configdb_field(duthost, "SAG|GLOBAL", "gateway_mac")
    pytest_assert(cfg_mac.lower() == SAG_MAC.lower(), "CONFIG_DB SAG MAC mismatch")
    sag_flag = get_configdb_field(duthost, "VLAN_INTERFACE|{}".format(vlan["name"]), "static_anycast_gateway")
    pytest_assert(
        sag_flag.lower() in ("true", "1"),
        "VLAN_INTERFACE static_anycast_gateway is not enabled")

    rif_macs = get_vlan_rif_mac_from_asic(duthost)
    pytest_assert(
        any(mac == SAG_MAC.lower() for mac in rif_macs),
        "VLAN router interface with SAG MAC was not programmed to ASIC_DB: {}".format(rif_macs))

    pytest_assert(
        kernel_addr_present(duthost, vlan["name"], vlan["ipv4"]),
        "IPv4 address missing on VLAN interface in kernel")
    pytest_assert(
        wait_until(20, 2, 1, ip2me_route_programmed, duthost, vlan["ipv4"]),
        "IPv4 IP2ME route was not programmed to ASIC")

    if vlan.get("ipv6"):
        pytest_assert(
            kernel_addr_present(duthost, vlan["name"], vlan["ipv6"]),
            "IPv6 address missing on VLAN interface in kernel")
        pytest_assert(
            wait_until(20, 2, 1, ip2me_route_programmed, duthost, vlan["ipv6"].split("/")[0]
                       if "/" in str(vlan["ipv6"]) else vlan["ipv6"]),
            "IPv6 IP2ME route was not programmed to ASIC")

    member = vlan["members"][0]
    host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
    host_ip = host_ip_from_gateway(vlan["ipv4"], 30)
    # ARP: host learns SAG virtual MAC; packets to SAG IP are trapped (DUT replies).
    arp_req = testutils.simple_arp_packet(
        eth_src=host_mac,
        eth_dst="ff:ff:ff:ff:ff:ff",
        arp_op=1,
        ip_snd=host_ip,
        ip_tgt=vlan["ipv4"],
        hw_snd=host_mac,
        hw_tgt="00:00:00:00:00:00",
    )
    arp_exp = testutils.simple_arp_packet(
        eth_src=SAG_MAC,
        eth_dst=host_mac,
        arp_op=2,
        ip_snd=vlan["ipv4"],
        ip_tgt=host_ip,
        hw_snd=SAG_MAC,
        hw_tgt=host_mac,
    )
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, member["ptf_index"], arp_req)
    testutils.verify_packet(ptfadapter, Mask(arp_exp), member["ptf_index"], timeout=8)

    icmp = testutils.simple_icmp_packet(
        eth_src=host_mac,
        eth_dst=SAG_MAC,
        ip_src=host_ip,
        ip_dst=vlan["ipv4"],
        icmp_type=8,
        icmp_code=0,
    )
    icmp_exp = testutils.simple_icmp_packet(
        eth_src=SAG_MAC,
        eth_dst=host_mac,
        ip_src=vlan["ipv4"],
        ip_dst=host_ip,
        icmp_type=0,
        icmp_code=0,
    )
    masked_icmp = Mask(icmp_exp)
    masked_icmp.set_do_not_care_scapy(scapy.IP, "id")
    masked_icmp.set_do_not_care_scapy(scapy.IP, "chksum")
    masked_icmp.set_do_not_care_scapy(scapy.ICMP, "chksum")
    ptfadapter.dataplane.flush()
    testutils.send(ptfadapter, member["ptf_index"], icmp)
    testutils.verify_packet(ptfadapter, masked_icmp, member["ptf_index"], timeout=8)

    pytest_assert(
        wait_until(20, 2, 0, lambda: dut_neighbor_mac(duthost, host_ip) == host_mac.lower()),
        "Switch did not learn host neighbor {} on {}".format(host_ip, vlan["name"]))


def test_sag_hld_disabled_reverts_to_cpu_mac(ptfadapter, sag_enabled):
    """
    HLD system case 2: disable SAG on the VLAN interface.
    Kernel and ASIC MAC revert to CPU MAC; neighbor learns CPU MAC.
    """
    topo = sag_enabled
    duthost = topo["duthost"]
    vlan = _ipv4_vlan(topo)
    try:
        set_sag_enabled(duthost, vlan["vlan_id"], False)
        wait_for_kernel_mac(duthost, vlan["name"], topo["router_mac"])
        pytest_assert(
            get_kernel_iface_mac(duthost, vlan["name"]) == topo["router_mac"],
            "VLAN MAC did not revert to CPU MAC in kernel")
        rif_macs = get_vlan_rif_mac_from_asic(duthost)
        pytest_assert(
            any(mac == topo["router_mac"] for mac in rif_macs),
            "VLAN RIF was not reprogrammed with CPU MAC: {}".format(rif_macs))

        member = vlan["members"][0]
        host_mac = ptfadapter.dataplane.get_mac(0, member["ptf_index"])
        host_ip = host_ip_from_gateway(vlan["ipv4"], 31)
        arp_req = testutils.simple_arp_packet(
            eth_src=host_mac,
            eth_dst="ff:ff:ff:ff:ff:ff",
            arp_op=1,
            ip_snd=host_ip,
            ip_tgt=vlan["ipv4"],
            hw_snd=host_mac,
            hw_tgt="00:00:00:00:00:00",
        )
        arp_cpu = testutils.simple_arp_packet(
            eth_src=topo["router_mac"],
            eth_dst=host_mac,
            arp_op=2,
            ip_snd=vlan["ipv4"],
            ip_tgt=host_ip,
            hw_snd=topo["router_mac"],
            hw_tgt=host_mac,
        )
        ptfadapter.dataplane.flush()
        testutils.send(ptfadapter, member["ptf_index"], arp_req)
        testutils.verify_packet(ptfadapter, Mask(arp_cpu), member["ptf_index"], timeout=8)
    finally:
        set_sag_enabled(duthost, vlan["vlan_id"], True)
        wait_for_kernel_mac(duthost, vlan["name"], SAG_MAC)
