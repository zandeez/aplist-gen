import datetime

from aplist_gen.core import APRecord, ScanEntry
from aplist_gen.planner import (
    CANDIDATES_24G,
    CANDIDATES_5G,
    CLUSTER_OVERLAP_WEIGHT,
    CO_CHANNEL_WEIGHT,
    DFS_CHANNELS,
    DFS_HEAVY_PENALTY,
    DFS_PENALTY,
    cluster_overlap_cost,
    dfs_penalty_for,
    external_overlap_cost,
    plan_channels,
)

NON_DFS_5G = [36, 40, 44, 48]


def test_plan_channels_keeps_ap_when_no_conflict():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6)]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert (recs[0].current_channel, recs[0].recommended_channel, recs[0].reason) == (6, 6, "")


def test_plan_channels_separates_conflicting_cluster_aps_on_24g():
    own_records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=1),
        APRecord(bssid="AA:AA:AA:AA:AA:02", essid="My Network", channel=1),
    ]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    first, second = recs
    assert (first.current_channel, first.recommended_channel) == (1, 1)
    assert second.current_channel == 1
    assert second.recommended_channel != 1
    assert "AA:AA:AA:AA:AA:01" in second.reason


def test_plan_channels_accounts_for_trusted_external_interference():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6)]
    external = [ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=6, rssi=-40)]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    rec = recs[0]
    assert rec.current_channel == 6
    assert rec.recommended_channel != 6
    assert "NeighboursWifi" in rec.reason


def test_plan_channels_prefers_non_dfs_when_costs_tie():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100)]
    external = [ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=100, rssi=-40)]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert recs[0].recommended_channel in NON_DFS_5G


def test_plan_channels_uses_dfs_when_non_dfs_channels_are_all_congested():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=36)]
    external = [
        ScanEntry(bssid=f"DE:AD:BE:EF:00:0{i}", essid=f"Neighbour{i}", channel=ch, rssi=-40)
        for i, ch in enumerate(NON_DFS_5G, start=1)
    ]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert recs[0].recommended_channel in DFS_CHANNELS


def test_plan_channels_skips_records_with_unknown_channel():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=None)]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert recs == []


def test_plan_channels_reason_mentions_dfs_when_move_has_no_interference_contributors():
    # Sole AP, no cluster/external interference at all -- the only reason
    # to move off its DFS current channel is the DFS penalty itself, so
    # the reason must say that rather than falling back to the generic
    # "lower overall interference cost" string.
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=52)]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    rec = recs[0]
    assert rec.current_channel == 52
    assert rec.recommended_channel not in DFS_CHANNELS
    assert "DFS" in rec.reason
    assert rec.reason != "lower overall interference cost"


def test_plan_channels_recommendation_includes_host():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6, host="192.0.2.80")]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert recs[0].host == "192.0.2.80"


def test_plan_channels_recommendation_host_defaults_to_none():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6)]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert recs[0].host is None


def test_plan_channels_reason_caps_contributors_and_shows_remainder_count():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6)]
    external = [
        ScanEntry(bssid=f"DE:AD:BE:EF:00:0{i}", essid=f"Neighbour{i}", channel=6, rssi=-40)
        for i in range(1, 6)
    ]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    reason = recs[0].reason
    assert reason.count("external network") == 3
    assert "and 2 more" in reason


def test_cluster_overlap_weight_is_double_co_channel_weight():
    # Own-cluster APs are ~5m apart in this deployment -- interference
    # between them matters more than the same amount of external
    # interference, since clients actively roam between your own APs.
    # Ratio confirmed with Andrew 2026-08-22.
    assert CLUSTER_OVERLAP_WEIGHT == 2 * CO_CHANNEL_WEIGHT


def test_cluster_overlap_costs_more_than_equivalent_external_overlap():
    assert cluster_overlap_cost(6, 6, "24g") == 2 * external_overlap_cost(6, 6, "24g")


def test_plan_channels_uses_the_one_clear_channel_for_only_one_ap():
    # Two cluster APs, both currently on congested channels, with channel
    # 13 clear of both external interference and their own conflict. The
    # globally cheapest move is for exactly one AP to take channel 13 and
    # the other to stay put and eat its own external congestion alone --
    # not for both to converge onto 13 and create their own overlap.
    own_records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=1),
        APRecord(bssid="AA:AA:AA:AA:AA:02", essid="My Network", channel=7),
    ]
    external = [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="Neighbour1", channel=1, rssi=-40),
        ScanEntry(bssid="DE:AD:BE:EF:00:02", essid="Neighbour2", channel=7, rssi=-40),
    ]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    recommended = {rec.recommended_channel for rec in recs}
    assert 13 in recommended
    assert len(recommended) == 2


def test_cluster_overlap_cost_treats_40mhz_bonded_pair_as_full_overlap():
    assert cluster_overlap_cost(36, 40, "5g") == cluster_overlap_cost(36, 36, "5g")


def test_cluster_overlap_cost_unpaired_5g_channels_dont_overlap():
    assert cluster_overlap_cost(36, 44, "5g") == 0


def test_dfs_penalty_for_returns_heavy_penalty_inside_hosts_critical_window():
    now = datetime.datetime(2026, 8, 21, 10, 0)  # 10am -- .78's day window

    assert dfs_penalty_for("192.0.2.78", now) == DFS_HEAVY_PENALTY


def test_dfs_penalty_for_returns_default_outside_hosts_critical_window():
    now = datetime.datetime(2026, 8, 21, 20, 0)  # 8pm -- outside .78's day window

    assert dfs_penalty_for("192.0.2.78", now) == DFS_PENALTY


def test_dfs_penalty_for_returns_default_for_host_without_a_window():
    now = datetime.datetime(2026, 8, 21, 10, 0)

    assert dfs_penalty_for("192.0.2.99", now) == DFS_PENALTY


def test_dfs_penalty_for_returns_heavy_penalty_inside_80s_evening_window():
    # Real radar-vacate + buffering incident, 2026-08-31 22:05 BST.
    now = datetime.datetime(2026, 8, 31, 22, 5)

    assert dfs_penalty_for("192.0.2.80", now) == DFS_HEAVY_PENALTY


def test_dfs_penalty_for_returns_default_outside_80s_evening_window():
    now = datetime.datetime(2026, 8, 31, 10, 0)

    assert dfs_penalty_for("192.0.2.80", now) == DFS_PENALTY


def test_dfs_penalty_for_returns_default_for_none_host():
    now = datetime.datetime(2026, 8, 21, 10, 0)

    assert dfs_penalty_for(None, now) == DFS_PENALTY


def test_plan_channels_heavy_dfs_penalty_changes_the_outcome_vs_default():
    # Same AP, same external congestion (cost 30 on every non-DFS 5GHz
    # candidate), only the clock differs. Outside .78's critical window the
    # default penalty (15) is cheaper than moving (30), so it stays on its
    # DFS channel; inside the window the heavy penalty (60) tips it into
    # moving. This proves the window-specific weighting actually reaches
    # plan_channels, not just dfs_penalty_for in isolation.
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78")]
    external = [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="Neighbour1", channel=36, rssi=-64),
        ScanEntry(bssid="DE:AD:BE:EF:00:02", essid="Neighbour2", channel=44, rssi=-64),
    ]

    outside_window = datetime.datetime(2026, 8, 21, 20, 0)
    inside_window = datetime.datetime(2026, 8, 21, 10, 0)

    default_recs = plan_channels(
        own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS, now=outside_window
    )
    heavy_recs = plan_channels(
        own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS, now=inside_window
    )

    assert default_recs[0].recommended_channel == 100
    assert heavy_recs[0].recommended_channel == 36


def test_plan_channels_global_search_sends_cheaper_penalty_radio_to_dfs():
    # Two radios collide on a DFS channel; every non-DFS pair is congested
    # by external networks. Global minimum is to move the EXPENSIVE-penalty
    # radio (.78, heavy window) off DFS and let the CHEAP-penalty radio
    # (.80, default) keep it -- moving the other way, or moving both,
    # costs strictly more in total. .78 sorts before .80 alphabetically by
    # bssid, so this also confirms the outcome isn't just an artifact of
    # processing order.
    now = datetime.datetime(2026, 8, 21, 10, 0)  # .78's heavy-penalty window
    own_records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=52, host="192.0.2.78"),
        APRecord(bssid="AA:AA:AA:AA:AA:02", essid="My Network", channel=52, host="192.0.2.80"),
    ]
    external = [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="Neighbour1", channel=36, rssi=-40),
        ScanEntry(bssid="DE:AD:BE:EF:00:02", essid="Neighbour2", channel=44, rssi=-40),
    ]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS, now=now)

    by_host = {rec.host: rec for rec in recs}
    assert by_host["192.0.2.78"].recommended_channel not in DFS_CHANNELS
    assert by_host["192.0.2.80"].recommended_channel == 52


def test_plan_channels_host_without_critical_window_always_uses_default_penalty():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.80")]
    external = [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="Neighbour1", channel=36, rssi=-64),
        ScanEntry(bssid="DE:AD:BE:EF:00:02", essid="Neighbour2", channel=44, rssi=-64),
    ]
    # Would be .78's critical window, but this record belongs to .80.
    now = datetime.datetime(2026, 8, 21, 10, 0)

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS, now=now)

    assert recs[0].recommended_channel == 100
