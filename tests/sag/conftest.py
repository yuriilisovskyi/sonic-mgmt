import logging
import pytest

from tests.common.config_reload import config_reload
from tests.sag.sag_helpers import (
    SAG_MAC,
    add_sag_mac,
    del_sag_mac,
    parse_vlan_interfaces,
    sag_cli_supported,
    set_sag_enabled,
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module", autouse=True)
def skip_if_no_sag(duthosts, rand_one_dut_hostname):
    duthost = duthosts[rand_one_dut_hostname]
    if not sag_cli_supported(duthost):
        pytest.skip("Static Anycast Gateway CLI is not supported on this image")


@pytest.fixture(scope="module")
def sag_topo(duthosts, rand_one_dut_hostname, tbinfo):
    duthost = duthosts[rand_one_dut_hostname]
    info = parse_vlan_interfaces(duthost, tbinfo)
    if not info["vlans"]:
        pytest.skip("No VLAN interfaces with PTF-mapped members found")
    info["duthost"] = duthost
    info["duthosts"] = duthosts
    info["tbinfo"] = tbinfo
    info["router_mac"] = duthost.facts["router_mac"].lower()
    return info


@pytest.fixture(scope="module")
def sag_enabled(sag_topo):
    """Enable SAG on all discovered VLAN interfaces and restore afterward."""
    duthost = sag_topo["duthost"]
    vlan_ids = [vlan["vlan_id"] for vlan in sag_topo["vlans"]]
    add_sag_mac(duthost, SAG_MAC)
    for vid in vlan_ids:
        set_sag_enabled(duthost, vid, True)
    # Dual-ToR: apply the same anycast gateway on the peer leaf when present.
    if len(sag_topo["duthosts"]) > 1:
        for peer in sag_topo["duthosts"]:
            if peer.hostname == duthost.hostname:
                continue
            if not sag_cli_supported(peer):
                continue
            add_sag_mac(peer, SAG_MAC)
            for vid in vlan_ids:
                set_sag_enabled(peer, vid, True)
    yield sag_topo
    for vid in vlan_ids:
        set_sag_enabled(duthost, vid, False)
    del_sag_mac(duthost, SAG_MAC)
    if len(sag_topo["duthosts"]) > 1:
        for peer in sag_topo["duthosts"]:
            if peer.hostname == duthost.hostname:
                continue
            for vid in vlan_ids:
                set_sag_enabled(peer, vid, False)
            del_sag_mac(peer, SAG_MAC)


@pytest.fixture(scope="module", autouse=True)
def restore_config_after_sag(duthosts):
    yield
    for duthost in duthosts:
        logger.info("Reloading minigraph to restore %s after SAG tests", duthost.hostname)
        config_reload(duthost, config_source="minigraph", safe_reload=True)
