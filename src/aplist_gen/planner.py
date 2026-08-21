import datetime
import itertools
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
MAX_REASON_CONTRIBUTORS = 3

# All radios in this cluster are confirmed running 40MHz HT channel bonding
# (there's no live SNMP field for a radio's own configured channel width on
# this hardware/firmware -- see poller.py). Two 20MHz primaries in the same
# bonded pair are the same physical channel and must score as a full
# overlap, not as independent candidates.
CHANNEL_40MHZ_PAIRS: dict[int, int] = {
    36: 40, 40: 36,
    44: 48, 48: 44,
    52: 56, 56: 52,
    60: 64, 64: 60,
    100: 104, 104: 100,
    108: 112, 112: 108,
    116: 120, 120: 116,
    124: 128, 128: 124,
    132: 136, 136: 132,
}

# Very heavy (but not absolute) DFS avoidance for specific APs during the
# hours a radar-vacate would disrupt them most -- half-open [start, end)
# local hours.
DFS_HEAVY_PENALTY = 60.0
CRITICAL_DFS_HOST_WINDOWS: dict[str, tuple[int, int]] = {
    "192.0.2.78": (6, 18),  # day
    "192.0.2.149": (18, 23),  # evening
}


@dataclass(frozen=True)
class ChannelRecommendation:
    bssid: str
    essid: str
    current_channel: int
    recommended_channel: int
    reason: str
    host: str | None = None


def band_of_channel(channel: int) -> str:
    return "24g" if channel <= 14 else "5g"


def _overlap_units(channel_a: int, channel_b: int, band: str) -> int:
    diff = abs(channel_a - channel_b)
    if band == "24g":
        return max(0, 5 - diff)
    if CHANNEL_40MHZ_PAIRS.get(channel_a) == channel_b:
        return 5
    return 5 if diff == 0 else 0


def cluster_overlap_cost(channel_a: int, channel_b: int, band: str) -> float:
    return _overlap_units(channel_a, channel_b, band) * CO_CHANNEL_WEIGHT


def _signal_weight(rssi: int | None) -> float:
    if rssi is None:
        return UNKNOWN_SIGNAL_WEIGHT
    return max(0.1, min(1.5, (rssi + 100) / 60))


def dfs_penalty_for(host: str | None, now: datetime.datetime) -> float:
    window = CRITICAL_DFS_HOST_WINDOWS.get(host) if host is not None else None
    if window is None:
        return DFS_PENALTY
    start, end = window
    if start <= now.hour < end:
        return DFS_HEAVY_PENALTY
    return DFS_PENALTY


def _external_cost(candidate: int, band: str, trusted_external: list[ScanEntry]) -> float:
    cost = 0.0
    for sighting in trusted_external:
        if band_of_channel(sighting.channel) != band:
            continue
        cost += cluster_overlap_cost(candidate, sighting.channel, band) * _signal_weight(sighting.rssi)
    return cost


def _total_band_cost(
    channels: dict[str, int],
    band: str,
    trusted_external: list[ScanEntry],
    dfs_channels: set[int],
    dfs_penalties: dict[str, float],
) -> float:
    cost = 0.0
    bssids = list(channels)
    for i, bssid_a in enumerate(bssids):
        channel_a = channels[bssid_a]
        for bssid_b in bssids[i + 1 :]:
            cost += cluster_overlap_cost(channel_a, channels[bssid_b], band)
        cost += _external_cost(channel_a, band, trusted_external)
        if channel_a in dfs_channels:
            cost += dfs_penalties[bssid_a]
    return cost


def _best_band_assignment(
    records: list[APRecord],
    band: str,
    candidates: list[int],
    trusted_external: list[ScanEntry],
    dfs_channels: set[int],
    dfs_penalties: dict[str, float],
) -> dict[str, int]:
    """Brute-force the lowest-total-cost channel combination across every
    radio in this band jointly, instead of deciding one radio at a time.

    Feasible at this cluster's scale: a handful of radios per band times a
    few dozen candidate channels at most is a few hundred thousand
    combinations, evaluated in well under a second.
    """
    bssids = sorted(r.bssid for r in records)
    current = {r.bssid: r.channel for r in records}

    def sort_key(channels: dict[str, int]) -> tuple[float, tuple[int, ...], tuple[int, ...]]:
        cost = _total_band_cost(channels, band, trusted_external, dfs_channels, dfs_penalties)
        # Tie-break 1: fewest radios changed from their current channel --
        # the same "no needless churn" principle the per-radio version had,
        # now applied cluster-wide instead of one radio at a time.
        changed = tuple(0 if channels[b] == current[b] else 1 for b in bssids)
        # Tie-break 2: deterministic final choice among equally-stable ties.
        chosen = tuple(channels[b] for b in bssids)
        return (cost, changed, chosen)

    best = current
    best_key = sort_key(best)
    for combo in itertools.product(candidates, repeat=len(bssids)):
        channels = dict(zip(bssids, combo))
        key = sort_key(channels)
        if key < best_key:
            best_key = key
            best = channels
    return best


def _contributors(
    channel: int,
    band: str,
    assigned: dict[str, int],
    trusted_external: list[ScanEntry],
) -> list[tuple[str, float]]:
    contributors: list[tuple[str, float]] = []
    for other_bssid, other_channel in assigned.items():
        if band_of_channel(other_channel) != band:
            continue
        cost = cluster_overlap_cost(channel, other_channel, band)
        if cost > 0:
            contributors.append((f"cluster AP {other_bssid} on channel {other_channel}", cost))
    for sighting in trusted_external:
        if band_of_channel(sighting.channel) != band:
            continue
        cost = cluster_overlap_cost(channel, sighting.channel, band) * _signal_weight(sighting.rssi)
        if cost > 0:
            rssi_text = f" ({sighting.rssi}dBm)" if sighting.rssi is not None else ""
            contributors.append(
                (f"external network '{sighting.essid}'{rssi_text} on channel {sighting.channel}", cost)
            )
    return contributors


def _build_reason(
    current_channel: int,
    recommended_channel: int,
    band: str,
    assigned: dict[str, int],
    trusted_external: list[ScanEntry],
    dfs_channels: set[int],
) -> str:
    parts: list[str] = []
    # A DFS-to-non-DFS move can be driven purely by DFS_PENALTY with zero
    # interference contributors -- say so explicitly rather than falling
    # through to the generic cost string, which would misattribute the
    # move to unspecified interference. A DFS-to-DFS move isn't "avoiding
    # DFS", so only the interference-contributor reasoning applies there.
    if current_channel in dfs_channels and recommended_channel not in dfs_channels:
        parts.append(f"channel {current_channel} is a DFS channel (radar-vacate risk)")
    contributors = _contributors(current_channel, band, assigned, trusted_external)
    if contributors:
        # In a busy area this list can run into the dozens -- show the
        # worst offenders by cost and summarize the rest, or the reason
        # stops being auditable and just becomes a wall of text.
        contributors.sort(key=lambda c: c[1], reverse=True)
        shown = [description for description, _ in contributors[:MAX_REASON_CONTRIBUTORS]]
        remainder = len(contributors) - len(shown)
        text = f"channel {current_channel} overlaps " + "; ".join(shown)
        if remainder > 0:
            text += f"; and {remainder} more"
        parts.append(text)
    if not parts:
        return "lower overall interference cost"
    return "; ".join(parts)


def plan_channels(
    own_records: list[APRecord],
    trusted_external: list[ScanEntry],
    candidates_24g: list[int],
    candidates_5g: list[int],
    dfs_channels: set[int],
    now: datetime.datetime | None = None,
) -> list[ChannelRecommendation]:
    now = now or datetime.datetime.now()
    known = [r for r in own_records if r.channel is not None]
    dfs_penalties = {r.bssid: dfs_penalty_for(r.host, now) for r in known}

    recommendations_by_bssid: dict[str, ChannelRecommendation] = {}
    for band, candidates in (("24g", candidates_24g), ("5g", candidates_5g)):
        band_records = [r for r in known if band_of_channel(r.channel) == band]
        if not band_records:
            continue
        best = _best_band_assignment(band_records, band, candidates, trusted_external, dfs_channels, dfs_penalties)

        for record in band_records:
            recommended = best[record.bssid]
            others = {bssid: channel for bssid, channel in best.items() if bssid != record.bssid}

            if recommended == record.channel:
                reason = ""
            else:
                reason = _build_reason(
                    record.channel, recommended, band, others, trusted_external, dfs_channels
                )

            recommendations_by_bssid[record.bssid] = ChannelRecommendation(
                bssid=record.bssid,
                essid=record.essid,
                current_channel=record.channel,
                recommended_channel=recommended,
                reason=reason,
                host=record.host,
            )

    return [recommendations_by_bssid[bssid] for bssid in sorted(recommendations_by_bssid)]
