from dataclasses import dataclass

from aplist_gen.core import APRecord, ScanEntry

CANDIDATES_24G: list[int] = [1, 6, 11]

# UNII-1: non-DFS on effectively every regulatory domain, including UK/ETSI.
NON_DFS_5G: list[int] = [36, 40, 44, 48]

# UNII-2/2e: DFS channels. Allowed as candidates but penalized in scoring
# (see DFS_PENALTY) -- coastal deployments see more weather/maritime radar,
# raising the odds of a forced radar-vacate on these channels.
DFS_CHANNELS: set[int] = {
    52, 56, 60, 64,
    100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140,
}

CANDIDATES_5G: list[int] = NON_DFS_5G + sorted(DFS_CHANNELS)

CO_CHANNEL_WEIGHT = 10.0
DFS_PENALTY = 15.0
UNKNOWN_SIGNAL_WEIGHT = 0.5


@dataclass(frozen=True)
class ChannelRecommendation:
    bssid: str
    essid: str
    current_channel: int
    recommended_channel: int
    reason: str


def band_of_channel(channel: int) -> str:
    return "24g" if channel <= 14 else "5g"


def _overlap_units(channel_a: int, channel_b: int, band: str) -> int:
    diff = abs(channel_a - channel_b)
    if band == "24g":
        return max(0, 5 - diff)
    return 5 if diff == 0 else 0


def cluster_overlap_cost(channel_a: int, channel_b: int, band: str) -> float:
    return _overlap_units(channel_a, channel_b, band) * CO_CHANNEL_WEIGHT


def _signal_weight(rssi: int | None) -> float:
    if rssi is None:
        return UNKNOWN_SIGNAL_WEIGHT
    return max(0.1, min(1.5, (rssi + 100) / 60))


def _candidate_cost(
    candidate: int,
    band: str,
    assigned: dict[str, int],
    trusted_external: list[ScanEntry],
    dfs_channels: set[int],
) -> float:
    cost = 0.0
    for other_channel in assigned.values():
        if band_of_channel(other_channel) != band:
            continue
        cost += cluster_overlap_cost(candidate, other_channel, band)
    for sighting in trusted_external:
        if band_of_channel(sighting.channel) != band:
            continue
        cost += cluster_overlap_cost(candidate, sighting.channel, band) * _signal_weight(sighting.rssi)
    if candidate in dfs_channels:
        cost += DFS_PENALTY
    return cost


def _contributors(
    channel: int,
    band: str,
    assigned: dict[str, int],
    trusted_external: list[ScanEntry],
) -> list[str]:
    contributors: list[str] = []
    for other_bssid, other_channel in assigned.items():
        if band_of_channel(other_channel) != band:
            continue
        if cluster_overlap_cost(channel, other_channel, band) > 0:
            contributors.append(f"cluster AP {other_bssid} on channel {other_channel}")
    for sighting in trusted_external:
        if band_of_channel(sighting.channel) != band:
            continue
        if cluster_overlap_cost(channel, sighting.channel, band) > 0:
            rssi_text = f" ({sighting.rssi}dBm)" if sighting.rssi is not None else ""
            contributors.append(
                f"external network '{sighting.essid}'{rssi_text} on channel {sighting.channel}"
            )
    return contributors


def _build_reason(
    current_channel: int,
    band: str,
    assigned: dict[str, int],
    trusted_external: list[ScanEntry],
) -> str:
    contributors = _contributors(current_channel, band, assigned, trusted_external)
    if not contributors:
        return "lower overall interference cost"
    return f"channel {current_channel} overlaps " + "; ".join(contributors)


def plan_channels(
    own_records: list[APRecord],
    trusted_external: list[ScanEntry],
    candidates_24g: list[int],
    candidates_5g: list[int],
    dfs_channels: set[int],
) -> list[ChannelRecommendation]:
    assigned: dict[str, int] = {}
    recommendations: list[ChannelRecommendation] = []

    for record in sorted(own_records, key=lambda r: r.bssid):
        if record.channel is None:
            continue
        band = band_of_channel(record.channel)
        candidates = candidates_24g if band == "24g" else candidates_5g

        current_cost = _candidate_cost(record.channel, band, assigned, trusted_external, dfs_channels)
        best_channel = record.channel
        best_cost = current_cost
        for candidate in candidates:
            if candidate == record.channel:
                continue
            cost = _candidate_cost(candidate, band, assigned, trusted_external, dfs_channels)
            if cost < best_cost:
                best_cost = cost
                best_channel = candidate

        if best_channel == record.channel:
            reason = ""
        else:
            reason = _build_reason(record.channel, band, assigned, trusted_external)

        assigned[record.bssid] = best_channel
        recommendations.append(
            ChannelRecommendation(
                bssid=record.bssid,
                essid=record.essid,
                current_channel=record.channel,
                recommended_channel=best_channel,
                reason=reason,
            )
        )

    return recommendations
