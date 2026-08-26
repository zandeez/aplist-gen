from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget

from aplist_gen.core import APRecord, parse_own_radio_tables
from aplist_gen.snmp_utils import collapse_to_radio_key, format_bssid, walk_bounded

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

# coDot11NumberOfUsers -- same coDot11AccessPointConfigTable as
# coDot11BSSID above, same <radio>.<vap> indexing.
USERS_OID = "1.3.6.1.4.1.8744.5.4.1.1.1.6"

# coDot11CurrentSNRLevel -- same coDot11AccessPointConfigTable as
# coDot11BSSID above, same <radio>.<vap> indexing.
SNR_OID = "1.3.6.1.4.1.8744.5.4.1.1.1.10"

# coDot11NoiseLevel (coDot11AssociationTable) -- indexed by
# <radio>.<associationIndex>, i.e. one row per currently-connected
# client, NOT one per radio. Live-verified 2026-08-25: value is
# identical across every client of the same radio (it's an ambient
# noise-floor reading, not per-client), so poll_ap collapses this to
# one value per radio below. Absent entirely for a radio with zero
# connected clients -- same "just don't emit the metric" handling as
# num_users.
NOISE_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.5"

# coDot11CountersTable (coDot11mac.2) -- indexed by ifIndex (radio)
# alone, no VAP component. This hardware has exactly one VAP per
# radio (confirmed throughout this project), so attaching a
# radio-level counter to that radio's sole APRecord is unambiguous.
RETRY_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.4"
MULTIPLE_RETRY_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.5"
FAILED_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.3"
ACK_FAILURE_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.9"

# Standard IF-MIB (RFC 1213), not Colubris-specific -- ifIndex 2 is the
# AP's wired Ethernet uplink ("eth0"), confirmed identical across all 3
# hosts on this cluster, 2026-08-26. Gives per-AP aggregate throughput
# (both radios combined), which is what "which room is busy" actually
# wants -- deliberately polled separately from poll_ap, not added to it:
# generate/plan never use this, and the metrics-expansion plan's final
# review already flagged (accepted, parked) that poll_ap's growing OID
# list makes those two commands pay SNMP cost for data they don't read.
UPLINK_ETH0_IFINDEX = "2"
IF_IN_OCTETS_OID = "1.3.6.1.2.1.2.2.1.10"
IF_OUT_OCTETS_OID = "1.3.6.1.2.1.2.2.1.16"


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
    raw_users = await walk_bounded(engine, community, target, USERS_OID)
    raw_snr = await walk_bounded(engine, community, target, SNR_OID)
    raw_noise = await walk_bounded(engine, community, target, NOISE_OID)
    raw_retry = await walk_bounded(engine, community, target, RETRY_COUNT_OID)
    raw_multiple_retry = await walk_bounded(engine, community, target, MULTIPLE_RETRY_COUNT_OID)
    raw_failed = await walk_bounded(engine, community, target, FAILED_COUNT_OID)
    raw_ack_failure = await walk_bounded(engine, community, target, ACK_FAILURE_COUNT_OID)

    bssids = {k: format_bssid(v) for k, v in raw_bssids.items()}
    essids = {k: v.prettyPrint() for k, v in raw_essids.items()}
    channels = {k: v.prettyPrint() for k, v in raw_channels.items()}
    users = {k: v.prettyPrint() for k, v in raw_users.items()}
    snr = {k: v.prettyPrint() for k, v in raw_snr.items()}
    retry_count = {k: v.prettyPrint() for k, v in raw_retry.items()}
    multiple_retry_count = {k: v.prettyPrint() for k, v in raw_multiple_retry.items()}
    failed_count = {k: v.prettyPrint() for k, v in raw_failed.items()}
    ack_failure_count = {k: v.prettyPrint() for k, v in raw_ack_failure.items()}

    # coDot11NoiseLevel is indexed per-client (<radio>.<associationIndex>);
    # collapse to one value per radio (identical across clients, see
    # NOISE_OID comment above) before it reaches parse_own_radio_tables,
    # which expects radio-only keys like channels/retry_count/etc.
    noise = collapse_to_radio_key({k: v.prettyPrint() for k, v in raw_noise.items()})

    return parse_own_radio_tables(
        bssids, essids, channels,
        host=host,
        users=users,
        snr=snr,
        noise=noise,
        retry_count=retry_count,
        multiple_retry_count=multiple_retry_count,
        failed_count=failed_count,
        ack_failure_count=ack_failure_count,
    )


async def poll_uplink_bytes(host: str, community: str, timeout: int = 3, retries: int = 1) -> tuple[int, int] | None:
    """Poll one AP's wired-uplink byte counters (rx, tx), or None if eth0's row is missing."""
    engine = SnmpEngine()
    target = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=retries)

    raw_in = await walk_bounded(engine, community, target, IF_IN_OCTETS_OID)
    raw_out = await walk_bounded(engine, community, target, IF_OUT_OCTETS_OID)

    in_value = raw_in.get(UPLINK_ETH0_IFINDEX)
    out_value = raw_out.get(UPLINK_ETH0_IFINDEX)
    if in_value is None or out_value is None:
        return None
    return int(in_value.prettyPrint()), int(out_value.prettyPrint())
