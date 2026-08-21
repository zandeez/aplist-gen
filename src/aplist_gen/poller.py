from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    walk_cmd,
)

from aplist_gen.core import APRecord, parse_own_radio_tables

BSSID_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.2"
ESSID_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.25"
CHAN_5G_OID = "1.3.6.1.4.1.8744.5.4.3.9.1.1"
CHAN_24G_OID = "1.3.6.1.4.1.8744.5.4.3.14.1.3"


async def _walk_bounded(engine, community: str, target, base_oid: str) -> dict[str, str]:
    """Walk base_oid, stopping as soon as a returned OID leaves that subtree.

    pysnmp's walk_cmd does not stop at a subtree boundary on its own --
    left unbounded it keeps walking the rest of the device's MIB.
    """
    results: dict[str, str] = {}
    async for error_indication, error_status, _error_index, var_binds in walk_cmd(
        engine,
        CommunityData(community),
        target,
        ContextData(),
        ObjectType(ObjectIdentity(base_oid)),
    ):
        if error_indication or error_status:
            break
        for var_bind in var_binds:
            oid_str = str(var_bind[0])
            if not oid_str.startswith(base_oid + "."):
                return results
            suffix = oid_str[len(base_oid) + 1 :]
            results[suffix] = var_bind[1]
    return results


def _format_bssid(value) -> str:
    return ":".join(f"{b:02X}" for b in value.asOctets())


async def poll_ap(host: str, community: str, timeout: int = 3, retries: int = 1) -> list[APRecord]:
    """Poll one HP MSM AP's own radio config over SNMP.

    Targets the AP's own configured radios/VAPs directly (not the RF
    neighbor-scan table) since self-hearing over RF is not reliable --
    see memory note orb_ap_cluster_is_hp_msm.md for how this was derived.
    """
    engine = SnmpEngine()
    target = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=retries)

    raw_bssids = await _walk_bounded(engine, community, target, BSSID_OID)
    raw_essids = await _walk_bounded(engine, community, target, ESSID_OID)
    raw_chan_5g = await _walk_bounded(engine, community, target, CHAN_5G_OID)
    raw_chan_24g = await _walk_bounded(engine, community, target, CHAN_24G_OID)

    bssids = {k: _format_bssid(v) for k, v in raw_bssids.items()}
    essids = {k: v.prettyPrint() for k, v in raw_essids.items()}
    chan_5g = {k: v.prettyPrint() for k, v in raw_chan_5g.items()}
    chan_24g = {k: v.prettyPrint() for k, v in raw_chan_24g.items()}

    return parse_own_radio_tables(bssids, essids, chan_5g, chan_24g)
