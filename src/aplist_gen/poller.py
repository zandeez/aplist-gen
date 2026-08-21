from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget

from aplist_gen.core import APRecord, parse_own_radio_tables
from aplist_gen.snmp_utils import format_bssid, walk_bounded

BSSID_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.2"
ESSID_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.25"
CHAN_5G_OID = "1.3.6.1.4.1.8744.5.4.3.9.1.1"
CHAN_24G_OID = "1.3.6.1.4.1.8744.5.4.3.14.1.3"


async def poll_ap(host: str, community: str, timeout: int = 3, retries: int = 1) -> list[APRecord]:
    """Poll one HP MSM AP's own radio config over SNMP.

    Targets the AP's own configured radios/VAPs directly (not the RF
    neighbor-scan table) since self-hearing over RF is not reliable --
    see memory note orb_ap_cluster_is_hp_msm.md for how this was derived.
    """
    engine = SnmpEngine()
    target = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=retries)

    raw_bssids = await walk_bounded(engine, community, target, BSSID_OID)
    raw_essids = await walk_bounded(engine, community, target, ESSID_OID)
    raw_chan_5g = await walk_bounded(engine, community, target, CHAN_5G_OID)
    raw_chan_24g = await walk_bounded(engine, community, target, CHAN_24G_OID)

    bssids = {k: format_bssid(v) for k, v in raw_bssids.items()}
    essids = {k: v.prettyPrint() for k, v in raw_essids.items()}
    chan_5g = {k: v.prettyPrint() for k, v in raw_chan_5g.items()}
    chan_24g = {k: v.prettyPrint() for k, v in raw_chan_24g.items()}

    return parse_own_radio_tables(bssids, essids, chan_5g, chan_24g)
