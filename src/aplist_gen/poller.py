from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget

from aplist_gen.core import APRecord, parse_own_radio_tables
from aplist_gen.snmp_utils import format_bssid, walk_bounded

# coDot11BSSID (COLUBRIS-802DOT11-MIB, coDot11AccessPointConfigTable) -- the
# AP's own configured VAP BSSID. Earlier versions of this poller read
# coDot11AssociationTable instead (connected client stations, not the AP's
# own VAP), which was discovered and corrected after live verification
# against production hardware on 2026-08-21.
BSSID_OID = "1.3.6.1.4.1.8744.5.4.1.1.1.11"

# coVirtualApSSID (COLUBRIS-VIRTUAL-AP-MIB) -- the VAP's configured SSID,
# indexed identically to coDot11BSSID above (coDot11AccessPointConfigTable
# AUGMENTS this table).
ESSID_OID = "1.3.6.1.4.1.8744.5.11.1.1.1.1.2"

# coDot11CurrentOperFrequency (COLUBRIS-802DOT11-MIB, coDot11PhyOperationTable)
# -- one table for both bands, keyed by radio index alone. Replaces an
# earlier band-split pair of OIDs (separate 5GHz/24GHz PHY-config tables)
# that was found on live hardware to silently omit a radio's channel while
# it was on a DFS channel (radar-vacate/CAC state) -- exactly the case this
# project's coastal DFS handling cares about most.
CHANNEL_OID = "1.3.6.1.4.1.8744.5.4.3.1.1.4"


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
    raw_channels = await walk_bounded(engine, community, target, CHANNEL_OID)

    bssids = {k: format_bssid(v) for k, v in raw_bssids.items()}
    essids = {k: v.prettyPrint() for k, v in raw_essids.items()}
    channels = {k: v.prettyPrint() for k, v in raw_channels.items()}

    return parse_own_radio_tables(bssids, essids, channels, host=host)
