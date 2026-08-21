import asyncio
import sys

from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget

from aplist_gen.core import ScanEntry, parse_neighbor_scan_table
from aplist_gen.snmp_utils import format_bssid, walk_bounded

# coDot11ScanTable from COLUBRIS-802DOT11-MIB, verified via live snmpwalk
# against an My Network HP MSM AP -- see Task 6 of
# docs/superpowers/plans/2026-08-21-channel-planning.md for how these
# were derived and verified.
NEIGHBOR_BSSID_OID = "1.3.6.1.4.1.8744.5.4.1.7.1.2"
NEIGHBOR_CHANNEL_OID = "1.3.6.1.4.1.8744.5.4.1.7.1.3"
NEIGHBOR_ESSID_OID = "1.3.6.1.4.1.8744.5.4.1.7.1.4"
NEIGHBOR_RSSI_OID = "1.3.6.1.4.1.8744.5.4.1.7.1.5"


async def poll_neighbors(host: str, community: str, own_essid: str, timeout: int = 3, retries: int = 1) -> list[ScanEntry]:
    """Poll one AP's RF neighbor-scan table for external (non-cluster) networks.

    This is the same table poller.poll_ap deliberately avoids for the
    cluster's own radio state (self-hearing was found unreliable on this
    hardware) -- here it's used only to detect other households' Wi-Fi,
    and results are further filtered through trust_cache before being
    trusted for scoring.
    """
    engine = SnmpEngine()
    target = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=retries)

    raw_bssids = await walk_bounded(engine, community, target, NEIGHBOR_BSSID_OID)
    raw_essids = await walk_bounded(engine, community, target, NEIGHBOR_ESSID_OID)
    raw_channels = await walk_bounded(engine, community, target, NEIGHBOR_CHANNEL_OID)
    raw_rssis = await walk_bounded(engine, community, target, NEIGHBOR_RSSI_OID)

    bssids = {k: format_bssid(v) for k, v in raw_bssids.items()}
    essids = {k: v.prettyPrint() for k, v in raw_essids.items()}
    channels = {k: v.prettyPrint() for k, v in raw_channels.items()}
    rssis = {k: v.prettyPrint() for k, v in raw_rssis.items()}

    return parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid=own_essid)


async def poll_all_neighbors(hosts: list[str], community: str, own_essid: str) -> list[ScanEntry]:
    results = await asyncio.gather(
        *(poll_neighbors(host, community, own_essid) for host in hosts), return_exceptions=True
    )

    entries: dict[str, ScanEntry] = {}
    for host, result in zip(hosts, results):
        if isinstance(result, Exception):
            print(f"warning: neighbor scan of {host} failed: {result}", file=sys.stderr)
            continue
        for entry in result:
            entries[entry.bssid] = entry
    return list(entries.values())
