from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    walk_cmd,
)


async def walk_bounded(engine, community: str, target, base_oid: str) -> dict[str, str]:
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


def format_bssid(value) -> str:
    return ":".join(f"{b:02X}" for b in value.asOctets())


def collapse_to_radio_key(raw: dict[str, str]) -> dict[str, str]:
    """Collapse an association-indexed dict (<radio>.<assocIndex> keys) to
    radio-only keys, keeping the first value seen per radio.

    Used for coDot11NoiseLevel, which reports the same ambient noise
    value on every one of a radio's currently-connected clients --
    "first wins" is equivalent to an average since the values are
    identical, not an arbitrary choice.
    """
    collapsed: dict[str, str] = {}
    for suffix, value in raw.items():
        radio = suffix.split(".", 1)[0]
        collapsed.setdefault(radio, value)
    return collapsed
