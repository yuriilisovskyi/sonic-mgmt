"""Helpers for Static Anycast Gateway (SAG) and Asymmetric IRB tests."""
import ipaddress
import json
import logging
import time

import pytest
from tests.common.helpers.assertions import pytest_assert
from tests.common.utilities import wait_until

logger = logging.getLogger(__name__)

SAG_MAC = "00:11:22:33:44:55"
SAG_MAC_ALT = "00:11:22:33:44:77"
VXLAN_UDP_PORT = 4789
REMOTE_VTEP_IP = "8.8.8.8"
VNI_BASE = 10000


def sag_cli_supported(duthost):
    """Return True if the SAG CLI is present on the DUT."""
    result = duthost.shell(
        "config static-anycast-gateway mac_address add -h",
        module_ignore_errors=True)
    if result["rc"] != 0:
        return False
    stderr = (result.get("stderr") or "") + (result.get("stdout") or "")
    if "No such command" in stderr or "invalid choice" in stderr.lower():
        return False
    return True


def add_sag_mac(duthost, mac):
    duthost.shell("config static-anycast-gateway mac_address add {}".format(mac))


def del_sag_mac(duthost, mac):
    duthost.shell(
        "config static-anycast-gateway mac_address del {}".format(mac),
        module_ignore_errors=True)


def set_sag_enabled(duthost, vlan_id, enable):
    action = "enable" if enable else "disable"
    duthost.shell("config vlan static-anycast-gateway {} {}".format(action, vlan_id))


def get_configdb_field(duthost, key, field):
    result = duthost.shell(
        'sonic-db-cli CONFIG_DB HGET "{}" {}'.format(key, field),
        module_ignore_errors=True)
    return (result.get("stdout") or "").strip()


def get_appldb_field(duthost, key, field):
    result = duthost.shell(
        'sonic-db-cli APPL_DB HGET "{}" {}'.format(key, field),
        module_ignore_errors=True)
    return (result.get("stdout") or "").strip()


def get_kernel_iface_mac(duthost, iface):
    result = duthost.shell("ip link show {}".format(iface))
    # Example: "2: Vlan1000: <BROADCAST,MULTICAST,UP> mtu 9100 ...\n    link/ether aa:bb:..."
    for line in result["stdout_lines"]:
        parts = line.split()
        if "link/ether" in parts:
            return parts[parts.index("link/ether") + 1].lower()
    return ""


def get_router_mac(duthost):
    return duthost.facts["router_mac"].lower()


def get_vlan_rif_mac_from_asic(duthost):
    """Return SRC MAC addresses programmed on VLAN router interfaces in ASIC_DB."""
    keys = duthost.shell(
        'sonic-db-cli ASIC_DB KEYS "ASIC_STATE:SAI_OBJECT_TYPE_ROUTER_INTERFACE:*"',
        module_ignore_errors=True)
    macs = []
    for key in keys.get("stdout_lines") or []:
        rif = duthost.shell(
            'sonic-db-cli ASIC_DB HGETALL "{}"'.format(key),
            module_ignore_errors=True)
        text = rif.get("stdout") or ""
        if "SAI_ROUTER_INTERFACE_TYPE_VLAN" not in text:
            continue
        lines = rif.get("stdout_lines") or []
        for idx, line in enumerate(lines):
            if line == "SAI_ROUTER_INTERFACE_ATTR_SRC_MAC_ADDRESS" and idx + 1 < len(lines):
                macs.append(lines[idx + 1].lower())
                break
    return macs


def ip2me_route_programmed(duthost, ip_addr):
    result = duthost.shell(
        'sonic-db-cli ASIC_DB KEYS "ASIC_STATE:SAI_OBJECT_TYPE_ROUTE_ENTRY:*{}*"'.format(ip_addr),
        module_ignore_errors=True)
    return bool(result.get("stdout_lines"))


def kernel_addr_present(duthost, iface, ip_addr):
    result = duthost.shell("ip addr show dev {}".format(iface), module_ignore_errors=True)
    return ip_addr in (result.get("stdout") or "")


def wait_for_kernel_mac(duthost, iface, expected_mac, timeout=30):
    expected = expected_mac.lower()

    def _check():
        return get_kernel_iface_mac(duthost, iface) == expected

    pytest_assert(
        wait_until(timeout, 2, 1, _check),
        "Kernel MAC on {} is {}, expected {}".format(
            iface, get_kernel_iface_mac(duthost, iface), expected))


def parse_vlan_interfaces(duthost, tbinfo):
    """Collect VLAN interfaces, members, and PTF indices from minigraph."""
    mg_facts = duthost.get_extended_minigraph_facts(tbinfo)
    vlans = []
    vlan_intfs = mg_facts.get("minigraph_vlan_interfaces") or []
    vlan_table = mg_facts.get("minigraph_vlans") or {}
    ptf_indices = mg_facts.get("minigraph_ptf_indices") or {}

    by_attach = {}
    for intf in vlan_intfs:
        name = intf["attachto"]
        by_attach.setdefault(name, {"ipv4": None, "ipv6": None, "name": name})
        addr = intf.get("addr")
        if addr and ":" in str(addr):
            by_attach[name]["ipv6"] = intf
        else:
            by_attach[name]["ipv4"] = intf

    for name, info in by_attach.items():
        vlan_meta = vlan_table.get(name) or {}
        vlan_id = int(str(vlan_meta.get("vlanid") or name.replace("Vlan", "")))
        members = list(vlan_meta.get("members") or [])
        member_ptf = []
        for member in members:
            if member in ptf_indices:
                member_ptf.append({"port": member, "ptf_index": ptf_indices[member]})
        if not member_ptf:
            continue
        ipv4 = info.get("ipv4") or {}
        vlans.append({
            "name": name,
            "vlan_id": vlan_id,
            "ipv4": ipv4.get("addr"),
            "ipv4_prefix": "{}/{}".format(ipv4.get("addr"), ipv4.get("prefixlen"))
            if ipv4.get("addr") else None,
            "ipv6": (info.get("ipv6") or {}).get("addr"),
            "members": member_ptf,
            "tagged": vlan_meta.get("type") == "tagged",
        })

    loopback_v4 = None
    for intf in mg_facts.get("minigraph_lo_interfaces") or []:
        addr = intf.get("addr")
        if addr and ":" not in str(addr):
            loopback_v4 = addr
            break

    uplink_ptf = []
    for pc in (mg_facts.get("minigraph_portchannels") or {}).values():
        for member in pc.get("members") or []:
            if member in ptf_indices:
                uplink_ptf.append(ptf_indices[member])
    if not uplink_ptf:
        vlan_member_ports = set()
        for vlan in vlans:
            vlan_member_ports.update(m["port"] for m in vlan["members"])
        for port, idx in ptf_indices.items():
            if port not in vlan_member_ports:
                uplink_ptf.append(idx)

    return {
        "vlans": vlans,
        "loopback_v4": loopback_v4,
        "uplink_ptf": sorted(set(uplink_ptf)),
        "ptf_indices": ptf_indices,
        "mg_facts": mg_facts,
    }


def vlan_vni(vlan_id):
    return VNI_BASE + int(vlan_id)


def apply_json_config(duthost, config, name="sag"):
    filename = "/tmp/{}-{}.json".format(name, int(time.time() * 1000))
    duthost.copy(content=json.dumps(config, indent=2), dest=filename)
    duthost.shell("sudo config load {} -y".format(filename))
    duthost.shell("rm -f {}".format(filename))


def configure_vxlan_maps(duthost, loopback_ip, vlan_ids, remote_vtep=REMOTE_VTEP_IP):
    """Create a VXLAN tunnel and VLAN-to-VNI maps."""
    tunnel_cfg = {
        "VXLAN_TUNNEL": {
            "tunnelVxlan": {
                "src_ip": loopback_ip
            }
        }
    }
    apply_json_config(duthost, tunnel_cfg, name="sag_vxlan_tunnel")
    maps = {"VXLAN_TUNNEL_MAP": {}}
    for vid in vlan_ids:
        maps["VXLAN_TUNNEL_MAP"]["tunnelVxlan|mapVlan{}".format(vid)] = {
            "vni": str(vlan_vni(vid)),
            "vlan": "Vlan{}".format(vid)
        }
    apply_json_config(duthost, maps, name="sag_vxlan_maps")
    return remote_vtep


def remove_vxlan_maps(duthost, vlan_ids):
    for vid in vlan_ids:
        duthost.shell(
            'sonic-db-cli CONFIG_DB DEL "VXLAN_TUNNEL_MAP|tunnelVxlan|mapVlan{}"'.format(vid),
            module_ignore_errors=True)
    duthost.shell('sonic-db-cli CONFIG_DB DEL "VXLAN_TUNNEL|tunnelVxlan"', module_ignore_errors=True)


def apply_swss_fdb(duthost, vlan_name, mac, port, op="SET"):
    """Program or delete a static FDB entry via swssconfig."""
    entry = {
        "FDB_TABLE:{}:{}".format(vlan_name, mac.replace(":", "-")): {
            "port": port,
            "type": "static"
        },
        "OP": op
    }
    dut_fdb_config = "/tmp/sag_fdb.json"
    duthost.copy(content=json.dumps([entry]), dest=dut_fdb_config)
    duthost.command("docker cp {} swss:/fdb.json".format(dut_fdb_config))
    duthost.command("docker exec swss sh -c 'swssconfig /fdb.json'")
    duthost.shell("rm -f {}".format(dut_fdb_config), module_ignore_errors=True)


def add_static_neighbor(duthost, ip_addr, mac, iface):
    if ":" in ip_addr:
        duthost.shell(
            "sudo ip -6 neigh replace {} lladdr {} dev {}".format(ip_addr, mac, iface))
    else:
        duthost.shell("sudo ip neigh replace {} lladdr {} dev {}".format(ip_addr, mac, iface))


def del_static_neighbor(duthost, ip_addr, iface):
    family = "-6 " if ":" in ip_addr else ""
    duthost.shell(
        "sudo ip {}neigh del {} dev {}".format(family, ip_addr, iface),
        module_ignore_errors=True)


def show_sag(duthost):
    return duthost.shell("show static-anycast-gateway", module_ignore_errors=True)


def dut_neighbor_mac(duthost, ip_addr):
    result = duthost.shell("ip neigh show {}".format(ip_addr), module_ignore_errors=True)
    for line in result.get("stdout_lines") or []:
        parts = line.split()
        if "lladdr" in parts:
            return parts[parts.index("lladdr") + 1].lower()
    return ""


def require_min_vlan_members(vlan, count, reason):
    pytest_assert(len(vlan["members"]) >= count, reason)


def host_ip_from_gateway(gateway_ip, host_id):
    """Derive a host address in the same /24 or IPv6 as the gateway."""
    if ":" in gateway_ip:
        prefix = gateway_ip.rsplit(":", 1)[0]
        return "{}:{}".format(prefix, host_id)
    parts = gateway_ip.split(".")
    parts[-1] = str(host_id)
    return ".".join(parts)


EXTRA_VLAN_ID_START = 200
EXTRA_VLAN_SUBNET_START = 200


def _portchannel_member_ports(mg_facts):
    members = set()
    for pc in (mg_facts.get("minigraph_portchannels") or {}).values():
        members.update(pc.get("members") or [])
    return members


def _pick_unused_vlan_id(existing_ids):
    vid = EXTRA_VLAN_ID_START
    while vid in existing_ids or vid in (0, 1, 4095):
        vid += 1
        pytest_assert(vid < 4095, "Could not find a free VLAN ID for inter-VLAN SAG setup")
    return vid


def _pick_unused_ipv4_prefix(existing_prefixes):
    networks = []
    for prefix in existing_prefixes:
        if not prefix or ":" in str(prefix):
            continue
        try:
            networks.append(ipaddress.ip_network(prefix, strict=False))
        except ValueError:
            continue
    for host_octet in range(EXTRA_VLAN_SUBNET_START, 250):
        candidate = "192.168.{}.1/24".format(host_octet)
        cand_net = ipaddress.ip_network(candidate, strict=False)
        if any(cand_net.overlaps(net) for net in networks):
            continue
        return candidate, "192.168.{}.1".format(host_octet)
    pytest.fail("Could not find a free IPv4 subnet for inter-VLAN SAG setup")


def _vlan_member_tagging(duthost, vlan_name, port):
    result = duthost.shell(
        'sonic-db-cli CONFIG_DB HGET "VLAN_MEMBER|{}|{}" tagging_mode'.format(vlan_name, port),
        module_ignore_errors=True)
    return (result.get("stdout") or "untagged").strip() or "untagged"


def _select_port_to_move(src_vlan, mg_facts):
    pytest_assert(
        len(src_vlan["members"]) >= 2,
        "Need at least two VLAN member ports to create a second VLAN for inter-VLAN routing")
    pc_members = _portchannel_member_ports(mg_facts)
    candidates = [m for m in src_vlan["members"] if m["port"] not in pc_members]
    if not candidates:
        candidates = list(src_vlan["members"])
    # Keep the first member on the original VLAN; move a later one.
    if candidates[0]["port"] == src_vlan["members"][0]["port"] and len(candidates) > 1:
        return candidates[-1]
    return candidates[-1]


def ensure_second_ipv4_vlan(duthost, sag_topo):
    """Create a second IPv4 VLAN when the DUT only has one, then enable SAG on it.

    A downlink is moved from the existing VLAN into a new untagged VLAN so
    inter-VLAN routing tests can run on t0 (typically a single Vlan1000).
    Returns the created VLAN dict, or None if two IPv4 VLANs already exist.
    """
    ipv4_vlans = [vlan for vlan in sag_topo["vlans"] if vlan.get("ipv4")]
    pytest_assert(ipv4_vlans, "Need at least one IPv4 VLAN to build inter-VLAN SAG topology")
    if len(ipv4_vlans) >= 2:
        return None

    src_vlan = ipv4_vlans[0]
    mg_facts = sag_topo.get("mg_facts") or {}
    moved = _select_port_to_move(src_vlan, mg_facts)
    tagging_mode = _vlan_member_tagging(duthost, src_vlan["name"], moved["port"])
    existing_ids = {vlan["vlan_id"] for vlan in sag_topo["vlans"]}
    vid = _pick_unused_vlan_id(existing_ids)
    existing_prefixes = [vlan.get("ipv4_prefix") for vlan in sag_topo["vlans"]]
    prefix, gateway = _pick_unused_ipv4_prefix(existing_prefixes)
    vlan_name = "Vlan{}".format(vid)

    logger.info(
        "Creating second IPv4 VLAN %s ip %s by moving %s from %s",
        vlan_name, prefix, moved["port"], src_vlan["name"])
    duthost.shell("config vlan member del {} {}".format(src_vlan["vlan_id"], moved["port"]))
    time.sleep(1)
    duthost.shell("config vlan add {}".format(vid))
    duthost.shell("config vlan member add -u {} {}".format(vid, moved["port"]))
    duthost.shell("config interface ip add {} {}".format(vlan_name, prefix))
    set_sag_enabled(duthost, vid, True)
    pytest_assert(
        wait_until(30, 2, 2, kernel_addr_present, duthost, vlan_name, gateway),
        "IPv4 address {} was not programmed on {}".format(gateway, vlan_name))
    wait_for_kernel_mac(duthost, vlan_name, SAG_MAC)

    src_vlan["members"] = [member for member in src_vlan["members"] if member["port"] != moved["port"]]
    created = {
        "name": vlan_name,
        "vlan_id": vid,
        "ipv4": gateway,
        "ipv4_prefix": prefix,
        "ipv6": None,
        "members": [moved],
        "tagged": False,
        "created_by_test": True,
        "orig_vlan_id": src_vlan["vlan_id"],
        "orig_port": moved["port"],
        "orig_tagging_mode": tagging_mode,
    }
    sag_topo["vlans"].append(created)
    sag_topo["created_vlan"] = created
    return created


def remove_created_ipv4_vlan(duthost, created):
    """Remove a VLAN created by ensure_second_ipv4_vlan and restore the original member."""
    if not created:
        return
    vid = created["vlan_id"]
    port = created["orig_port"]
    orig_vid = created["orig_vlan_id"]
    prefix = created["ipv4_prefix"]
    logger.info("Removing test VLAN Vlan%s and restoring %s to VLAN %s", vid, port, orig_vid)
    set_sag_enabled(duthost, vid, False)
    duthost.shell("config interface ip remove Vlan{} {}".format(vid, prefix), module_ignore_errors=True)
    duthost.shell("config vlan member del {} {}".format(vid, port), module_ignore_errors=True)
    duthost.shell("config vlan del {}".format(vid), module_ignore_errors=True)
    tagged = created.get("orig_tagging_mode") == "tagged"
    member_cmd = "config vlan member add {} {} {}".format(
        "" if tagged else "-u", orig_vid, port)
    duthost.shell(" ".join(member_cmd.split()), module_ignore_errors=True)
