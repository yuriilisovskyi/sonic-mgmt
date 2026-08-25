"""Helpers for Static Anycast Gateway (SAG) and Asymmetric IRB tests."""
import json
import logging
import time

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
