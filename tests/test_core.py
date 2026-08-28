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
    # keyed by "<radio>.<vap>", the current-channel table by "<radio>"
    # alone -- one unified table for both bands (coDot11CurrentOperFrequency),
    # not a 5GHz/24GHz split (that split table was found to silently omit a
    # radio mid-DFS-CAC on live hardware).
    bssids = {"4.1": "AA:AA:AA:AA:AA:01", "5.1": "AA:AA:AA:AA:AA:02"}
    essids = {"4.1": "My Network", "5.1": "My Network"}
    channels = {"4": "100", "5": "13"}

    records = parse_own_radio_tables(bssids, essids, channels, host="192.0.2.80")

    assert records == [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.80"),
        APRecord(bssid="AA:AA:AA:AA:AA:02", essid="My Network", channel=13, host="192.0.2.80"),
    ]


def test_parse_own_radio_tables_host_defaults_to_none():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}

    records = parse_own_radio_tables(bssids, essids, channels)

    assert records[0].host is None


def test_parse_own_radio_tables_skips_vap_with_no_channel_for_its_radio():
    bssids = {"6.1": "AA:AA:AA:AA:AA:03"}
    essids = {"6.1": "My Network"}

    records = parse_own_radio_tables(bssids, essids, channels={})

    assert records == []


def test_parse_own_radio_tables_rejects_impossible_band_for_radio():
    # Radio "5" is the 2.4GHz-only radio (RADIO_BAND) -- a live-observed
    # anomaly (2026-08-25, 2026-08-27) briefly reported a 5GHz channel
    # number for it. This must be dropped, not trusted.
    bssids = {"5.1": "AA:AA:AA:AA:AA:02"}
    essids = {"5.1": "My Network"}
    channels = {"5": "40"}  # 40 is a 5GHz channel, impossible for radio 5

    records = parse_own_radio_tables(bssids, essids, channels, host="192.0.2.78")

    assert records == []


def test_parse_own_radio_tables_rejects_impossible_band_other_radio():
    # Radio "4" is the 5GHz-only radio -- a 2.4GHz channel number there
    # is equally impossible.
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "6"}

    records = parse_own_radio_tables(bssids, essids, channels, host="192.0.2.80")

    assert records == []


def test_parse_own_radio_tables_keeps_valid_band_for_known_radios():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01", "5.1": "AA:AA:AA:AA:AA:02"}
    essids = {"4.1": "My Network", "5.1": "My Network"}
    channels = {"4": "100", "5": "13"}

    records = parse_own_radio_tables(bssids, essids, channels, host="192.0.2.80")

    assert len(records) == 2


def test_parse_own_radio_tables_does_not_validate_unknown_radio_index():
    # RADIO_BAND only covers the two radios this hardware actually has --
    # an unrecognized radio index (e.g. future hardware) isn't rejected
    # just because it isn't in the known mapping.
    bssids = {"6.1": "AA:AA:AA:AA:AA:03"}
    essids = {"6.1": "My Network"}
    channels = {"6": "40"}

    records = parse_own_radio_tables(bssids, essids, channels, host="192.0.2.80")

    assert len(records) == 1


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


def test_parse_own_radio_tables_includes_num_users():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}
    users = {"4.1": "3"}

    records = parse_own_radio_tables(bssids, essids, channels, users=users)

    assert records[0].num_users == 3


def test_parse_own_radio_tables_num_users_keyed_by_radio_only_does_not_match():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}
    users = {"4": "3"}  # radio-only key -- wrong shape, must not match "4.1"

    records = parse_own_radio_tables(bssids, essids, channels, users=users)

    assert records[0].num_users is None


def test_parse_own_radio_tables_num_users_defaults_to_none_when_missing():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}

    records = parse_own_radio_tables(bssids, essids, channels)

    assert records[0].num_users is None


def test_parse_own_radio_tables_includes_snr():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}
    snr = {"4.1": "35"}

    records = parse_own_radio_tables(bssids, essids, channels, snr=snr)

    assert records[0].snr_db == 35


def test_parse_own_radio_tables_snr_keyed_by_radio_only_does_not_match():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}
    snr = {"4": "35"}  # radio-only key -- wrong shape, must not match "4.1"

    records = parse_own_radio_tables(bssids, essids, channels, snr=snr)

    assert records[0].snr_db is None


def test_parse_own_radio_tables_includes_radio_keyed_counters():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}
    noise = {"4": "-86"}
    retry_count = {"4": "106268"}
    multiple_retry_count = {"4": "11018"}
    failed_count = {"4": "113060"}
    ack_failure_count = {"4": "113060"}

    records = parse_own_radio_tables(
        bssids, essids, channels,
        noise=noise,
        retry_count=retry_count,
        multiple_retry_count=multiple_retry_count,
        failed_count=failed_count,
        ack_failure_count=ack_failure_count,
    )

    assert records[0].noise_dbm == -86
    assert records[0].retry_count == 106268
    assert records[0].multiple_retry_count == 11018
    assert records[0].failed_count == 113060
    assert records[0].ack_failure_count == 113060


def test_parse_own_radio_tables_new_fields_default_to_none():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}

    records = parse_own_radio_tables(bssids, essids, channels)

    assert records[0].snr_db is None
    assert records[0].noise_dbm is None
    assert records[0].retry_count is None
    assert records[0].multiple_retry_count is None
    assert records[0].failed_count is None
    assert records[0].ack_failure_count is None
