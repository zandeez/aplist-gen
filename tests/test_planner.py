from aplist_gen.core import APRecord, ScanEntry
from aplist_gen.planner import CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS, plan_channels

NON_DFS_5G = [36, 40, 44, 48]


def test_plan_channels_keeps_ap_when_no_conflict():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6)]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert (recs[0].current_channel, recs[0].recommended_channel, recs[0].reason) == (6, 6, "")


def test_plan_channels_separates_conflicting_cluster_aps_on_24g():
    own_records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6),
        APRecord(bssid="AA:AA:AA:AA:AA:02", essid="My Network", channel=6),
    ]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    first, second = recs
    assert (first.current_channel, first.recommended_channel) == (6, 6)
    assert second.current_channel == 6
    assert second.recommended_channel != 6
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
