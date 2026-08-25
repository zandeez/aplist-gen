from aplist_gen.core import APRecord, ScanEntry
from aplist_gen.metrics import (
    format_neighbor_metrics,
    format_own_radio_metrics,
    format_poll_failures,
)


def test_format_own_radio_metrics_includes_channel_and_users():
    records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78", num_users=3),
    ]

    text = format_own_radio_metrics(records)

    assert 'aplist_own_channel{host="192.0.2.78",bssid="AA:AA:AA:AA:AA:01",essid="My Network"} 100' in text
    assert 'aplist_connected_users{host="192.0.2.78",bssid="AA:AA:AA:AA:AA:01",essid="My Network"} 3' in text


def test_format_own_radio_metrics_omits_users_line_when_unknown():
    records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78"),
    ]

    text = format_own_radio_metrics(records)

    assert "aplist_own_channel" in text
    assert "aplist_connected_users" not in text


def test_format_own_radio_metrics_skips_records_with_unknown_channel():
    records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=None)]

    text = format_own_radio_metrics(records)

    assert text == ""


def test_format_neighbor_metrics_aggregates_by_channel_not_by_device():
    sightings = [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="Neighbour1", channel=6, rssi=-40),
        ScanEntry(bssid="DE:AD:BE:EF:00:02", essid="Neighbour2", channel=6, rssi=-70),
        ScanEntry(bssid="DE:AD:BE:EF:00:03", essid="Neighbour3", channel=11, rssi=-60),
    ]

    text = format_neighbor_metrics(sightings)

    assert 'aplist_external_network_count{channel="6",band="24g"} 2' in text
    assert 'aplist_external_signal_dbm_max{channel="6",band="24g"} -40' in text
    assert 'aplist_external_network_count{channel="11",band="24g"} 1' in text
    assert "DE:AD:BE:EF:00:01" not in text
    assert "Neighbour1" not in text


def test_format_neighbor_metrics_handles_missing_rssi():
    sightings = [ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="Neighbour1", channel=6)]

    text = format_neighbor_metrics(sightings)

    assert 'aplist_external_network_count{channel="6",band="24g"} 1' in text
    assert "aplist_external_signal_dbm_max" not in text


def test_format_poll_failures_marks_failed_and_healthy_hosts():
    text = format_poll_failures(
        hosts=["192.0.2.80", "192.0.2.149"], failed_hosts=["192.0.2.149"]
    )

    assert 'aplist_poll_failure{host="192.0.2.80"} 0' in text
    assert 'aplist_poll_failure{host="192.0.2.149"} 1' in text


def test_format_own_radio_metrics_includes_snr_and_noise():
    records = [
        APRecord(
            bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78",
            snr_db=35, noise_dbm=-86,
        ),
    ]

    text = format_own_radio_metrics(records)

    assert 'aplist_snr_db{host="192.0.2.78",bssid="AA:AA:AA:AA:AA:01",essid="My Network"} 35' in text
    assert 'aplist_noise_floor_dbm{host="192.0.2.78",bssid="AA:AA:AA:AA:AA:01",essid="My Network"} -86' in text


def test_format_own_radio_metrics_includes_error_counters():
    records = [
        APRecord(
            bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78",
            retry_count=106268, multiple_retry_count=11018, failed_count=113060, ack_failure_count=113060,
        ),
    ]

    text = format_own_radio_metrics(records)

    labels = 'host="192.0.2.78",bssid="AA:AA:AA:AA:AA:01",essid="My Network"'
    assert f'aplist_retry_count{{{labels}}} 106268' in text
    assert f'aplist_multiple_retry_count{{{labels}}} 11018' in text
    assert f'aplist_failed_count{{{labels}}} 113060' in text
    assert f'aplist_ack_failure_count{{{labels}}} 113060' in text


def test_format_own_radio_metrics_omits_new_metrics_when_unknown():
    records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78"),
    ]

    text = format_own_radio_metrics(records)

    assert "aplist_snr_db" not in text
    assert "aplist_noise_floor_dbm" not in text
    assert "aplist_retry_count" not in text
    assert "aplist_multiple_retry_count" not in text
    assert "aplist_failed_count" not in text
    assert "aplist_ack_failure_count" not in text
