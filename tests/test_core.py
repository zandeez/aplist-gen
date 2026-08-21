from aplist_gen.core import (
    APRecord,
    ScanEntry,
    filter_cluster_aps,
    parse_neighbor_scan_table,
    parse_own_radio_tables,
    to_simple_ap_list_text,
    write_aplist_xml,
)


def test_filter_cluster_aps_keeps_matching_essid():
    entries = [
        ScanEntry(bssid="02:00:00:00:00:01", essid="My Network", channel=132),
    ]

    records = filter_cluster_aps(entries, target_essid="My Network")

    assert records == [APRecord(bssid="02:00:00:00:00:01", essid="My Network", channel=132)]


def test_filter_cluster_aps_drops_other_essids():
    # A real RF neighbor scan on a home network picks up next-door's APs
    # too -- those must never end up in aplist.xml.
    entries = [
        ScanEntry(bssid="02:00:00:00:00:01", essid="My Network", channel=132),
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=44),
    ]

    records = filter_cluster_aps(entries, target_essid="My Network")

    assert records == [APRecord(bssid="02:00:00:00:00:01", essid="My Network", channel=132)]


def test_filter_cluster_aps_dedupes_by_bssid():
    # The same physical BSSID is commonly heard by more than one local
    # radio's scanner (cross-radio detection), producing duplicate rows
    # for the same AP with the same channel.
    entries = [
        ScanEntry(bssid="02:00:00:00:00:01", essid="My Network", channel=132),
        ScanEntry(bssid="02:00:00:00:00:01", essid="My Network", channel=132),
    ]

    records = filter_cluster_aps(entries, target_essid="My Network")

    assert records == [APRecord(bssid="02:00:00:00:00:01", essid="My Network", channel=132)]


def test_parse_own_radio_tables_joins_by_radio_and_vap_suffix():
    # Modeled on a real live poll of an HP MSM560: BSSID/ESSID tables are
    # keyed by "<radio>.<vap>", channel tables by "<radio>" alone, and only
    # one of the two channel tables has an entry for a given radio (5GHz vs
    # 2.4GHz radios live in genuinely separate MIB branches on this MIB).
    bssids = {"4.1": "AA:AA:AA:AA:AA:01", "5.1": "AA:AA:AA:AA:AA:02"}
    essids = {"4.1": "My Network", "5.1": "My Network"}
    chan_5g = {"4": "100"}
    chan_24g = {"5": "13"}

    records = parse_own_radio_tables(bssids, essids, chan_5g, chan_24g)

    assert records == [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100),
        APRecord(bssid="AA:AA:AA:AA:AA:02", essid="My Network", channel=13),
    ]


def test_parse_own_radio_tables_skips_vap_with_no_channel_for_its_radio():
    bssids = {"6.1": "AA:AA:AA:AA:AA:03"}
    essids = {"6.1": "My Network"}

    records = parse_own_radio_tables(bssids, essids, chan_5g={}, chan_24g={})

    assert records == []


def test_to_simple_ap_list_text_format():
    records = [
        APRecord(bssid="02:00:00:00:00:02", essid="My Network", channel=None),
        APRecord(bssid="02:00:00:00:00:03", essid="My Network", channel=132),
    ]

    text = to_simple_ap_list_text(records)

    assert text == (
        "<?xml version='1.0'?>\n"
        "<simple-ap-list>\n"
        "# MAC SSID\n"
        '02:00:00:00:00:02 "My Network"\n'
        '02:00:00:00:00:03 "My Network" 132\n'
        "</simple-ap-list>\n"
    )


def test_write_aplist_xml_format():
    records = [APRecord(bssid="02:00:00:00:00:02", essid="My Network", channel=132)]

    xml = write_aplist_xml(records)

    assert '<ap mac="02:00:00:00:00:02" ssid="My Network" freq="132"/>' in xml
    assert xml.startswith("<?xml version='1.0'?>")


def test_scan_entry_rssi_defaults_to_none():
    entry = ScanEntry(bssid="02:00:00:00:00:01", essid="My Network", channel=132)

    assert entry.rssi is None


def test_scan_entry_accepts_rssi():
    entry = ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=44, rssi=-52)

    assert entry.rssi == -52


def test_parse_neighbor_scan_table_excludes_own_essid():
    bssids = {"1": "02:00:00:00:00:01"}
    essids = {"1": "My Network"}
    channels = {"1": "132"}
    rssis = {"1": "-50"}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == []


def test_parse_neighbor_scan_table_includes_external_with_rssi():
    bssids = {"1": "DE:AD:BE:EF:00:01"}
    essids = {"1": "NeighboursWifi"}
    channels = {"1": "44"}
    rssis = {"1": "-52"}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=44, rssi=-52)
    ]


def test_parse_neighbor_scan_table_defaults_missing_rssi_to_none():
    bssids = {"1": "DE:AD:BE:EF:00:01"}
    essids = {"1": "NeighboursWifi"}
    channels = {"1": "44"}
    rssis: dict[str, str] = {}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=44, rssi=None)
    ]


def test_parse_neighbor_scan_table_skips_row_with_no_channel():
    bssids = {"1": "DE:AD:BE:EF:00:01"}
    essids = {"1": "NeighboursWifi"}
    channels: dict[str, str] = {}
    rssis: dict[str, str] = {}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == []


def test_parse_neighbor_scan_table_skips_row_with_unparseable_channel():
    # The neighbor-scan table is known-unreliable in production (~110 rows
    # per host); one malformed row must not discard the other good rows.
    bssids = {"1": "DE:AD:BE:EF:00:01", "2": "DE:AD:BE:EF:00:02"}
    essids = {"1": "NeighboursWifi", "2": "OtherWifi"}
    channels = {"1": "", "2": "44"}
    rssis = {"1": "-52", "2": "-60"}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == [
        ScanEntry(bssid="DE:AD:BE:EF:00:02", essid="OtherWifi", channel=44, rssi=-60)
    ]


def test_parse_neighbor_scan_table_falls_back_to_none_rssi_when_unparseable():
    bssids = {"1": "DE:AD:BE:EF:00:01"}
    essids = {"1": "NeighboursWifi"}
    channels = {"1": "44"}
    rssis = {"1": ""}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=44, rssi=None)
    ]
