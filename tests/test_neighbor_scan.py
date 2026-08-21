import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aplist_gen.core import ScanEntry
from aplist_gen.neighbor_scan import poll_all_neighbors, poll_neighbors


@pytest.fixture
def mock_snmp_objects():
    """Create mock SNMP objects that behave like pysnmp objects."""
    # Mock BSSID as bytes-like object
    mock_bssid = MagicMock()
    mock_bssid.asOctets.return_value = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])

    # Mock string values with prettyPrint
    mock_essid = MagicMock()
    mock_essid.prettyPrint.return_value = "TestWiFi"

    mock_channel = MagicMock()
    mock_channel.prettyPrint.return_value = "36"

    mock_rssi = MagicMock()
    mock_rssi.prettyPrint.return_value = "-50"

    return {
        "bssid": mock_bssid,
        "essid": mock_essid,
        "channel": mock_channel,
        "rssi": mock_rssi,
    }


@pytest.mark.asyncio
async def test_poll_neighbors_returns_list_of_scan_entries(mock_snmp_objects):
    """Test that poll_neighbors returns a list of ScanEntry objects."""
    with patch("aplist_gen.neighbor_scan.SnmpEngine"), patch(
        "aplist_gen.neighbor_scan.UdpTransportTarget.create"
    ), patch("aplist_gen.neighbor_scan.walk_bounded") as mock_walk:
        # Setup mock walk_bounded to return SNMP objects
        mock_walk.side_effect = [
            {"1": mock_snmp_objects["bssid"]},  # raw_bssids
            {"1": mock_snmp_objects["essid"]},  # raw_essids
            {"1": mock_snmp_objects["channel"]},  # raw_channels
            {"1": mock_snmp_objects["rssi"]},  # raw_rssis
        ]

        result = await poll_neighbors("192.0.2.1", "public", "My Network")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].bssid == "AA:BB:CC:DD:EE:FF"
        assert result[0].essid == "TestWiFi"
        assert result[0].channel == 36
        assert result[0].rssi == -50


@pytest.mark.asyncio
async def test_poll_neighbors_filters_own_essid(mock_snmp_objects):
    """Test that poll_neighbors excludes entries matching own_essid."""
    mock_essid_own = MagicMock()
    mock_essid_own.prettyPrint.return_value = "My Network"

    with patch("aplist_gen.neighbor_scan.SnmpEngine"), patch(
        "aplist_gen.neighbor_scan.UdpTransportTarget.create"
    ), patch("aplist_gen.neighbor_scan.walk_bounded") as mock_walk:
        mock_walk.side_effect = [
            {"1": mock_snmp_objects["bssid"]},  # raw_bssids
            {"1": mock_essid_own},  # raw_essids (own ESSID)
            {"1": mock_snmp_objects["channel"]},  # raw_channels
            {"1": mock_snmp_objects["rssi"]},  # raw_rssis
        ]

        result = await poll_neighbors("192.0.2.1", "public", "My Network")

        assert len(result) == 0


@pytest.mark.asyncio
async def test_poll_neighbors_handles_missing_rssi(mock_snmp_objects):
    """Test that poll_neighbors handles missing RSSI values gracefully."""
    with patch("aplist_gen.neighbor_scan.SnmpEngine"), patch(
        "aplist_gen.neighbor_scan.UdpTransportTarget.create"
    ), patch("aplist_gen.neighbor_scan.walk_bounded") as mock_walk:
        mock_walk.side_effect = [
            {"1": mock_snmp_objects["bssid"]},  # raw_bssids
            {"1": mock_snmp_objects["essid"]},  # raw_essids
            {"1": mock_snmp_objects["channel"]},  # raw_channels
            {},  # raw_rssis (empty, no RSSI data)
        ]

        result = await poll_neighbors("192.0.2.1", "public", "My Network")

        assert len(result) == 1
        assert result[0].rssi is None


@pytest.mark.asyncio
async def test_poll_all_neighbors_deduplicates_by_bssid():
    """Test that poll_all_neighbors deduplicates entries by BSSID."""
    mock_entry1 = ScanEntry(bssid="AA:BB:CC:DD:EE:FF", essid="TestWiFi", channel=36, rssi=-50)
    mock_entry2 = ScanEntry(
        bssid="AA:BB:CC:DD:EE:FF", essid="TestWiFi", channel=36, rssi=-60
    )  # Different RSSI

    call_count = 0

    async def side_effect_func(*args, **kwargs):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return [mock_entry1]
        else:
            return [mock_entry2]

    with patch("aplist_gen.neighbor_scan.poll_neighbors", side_effect=side_effect_func):
        result = await poll_all_neighbors(["192.0.2.1", "192.0.2.2"], "public", "My Network")

        assert len(result) == 1
        assert result[0].bssid == "AA:BB:CC:DD:EE:FF"
        # Last seen value (from second host) should win
        assert result[0].rssi == -60


@pytest.mark.asyncio
async def test_poll_all_neighbors_handles_errors_gracefully(capsys):
    """Test that poll_all_neighbors continues when individual host polling fails."""
    mock_entry1 = ScanEntry(bssid="AA:BB:CC:DD:EE:FF", essid="TestWiFi", channel=36, rssi=-50)
    test_exception = ConnectionError("Connection refused")

    call_count = 0

    async def side_effect_func(*args, **kwargs):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            raise test_exception
        else:
            return [mock_entry1]

    with patch("aplist_gen.neighbor_scan.poll_neighbors", side_effect=side_effect_func):
        result = await poll_all_neighbors(["192.0.2.1", "192.0.2.2"], "public", "My Network")

        assert len(result) == 1
        assert result[0].bssid == "AA:BB:CC:DD:EE:FF"

        # Check that error was printed to stderr
        captured = capsys.readouterr()
        assert "warning: neighbor scan of 192.0.2.1 failed" in captured.err


@pytest.mark.asyncio
async def test_poll_all_neighbors_returns_empty_on_all_failures(capsys):
    """Test that poll_all_neighbors returns empty list when all hosts fail."""
    test_exception1 = ConnectionError("Connection refused")
    test_exception2 = TimeoutError("Timeout")

    with patch("aplist_gen.neighbor_scan.poll_neighbors") as mock_poll:
        mock_poll.side_effect = [
            test_exception1,
            test_exception2,
        ]

        result = await poll_all_neighbors(["192.0.2.1", "192.0.2.2"], "public", "My Network")

        assert len(result) == 0

        # Check that both errors were printed
        captured = capsys.readouterr()
        assert "warning: neighbor scan of 192.0.2.1 failed" in captured.err
        assert "warning: neighbor scan of 192.0.2.2 failed" in captured.err


@pytest.mark.asyncio
async def test_poll_all_neighbors_with_empty_hosts_list():
    """Test that poll_all_neighbors handles empty hosts list."""
    result = await poll_all_neighbors([], "public", "My Network")

    assert result == []


@pytest.mark.asyncio
async def test_poll_all_neighbors_multiple_unique_entries():
    """Test that poll_all_neighbors correctly combines multiple unique entries."""
    mock_entry1 = ScanEntry(bssid="AA:BB:CC:DD:EE:FF", essid="Network1", channel=36, rssi=-50)
    mock_entry2 = ScanEntry(bssid="BB:CC:DD:EE:FF:AA", essid="Network2", channel=44, rssi=-60)

    call_count = 0

    async def side_effect_func(*args, **kwargs):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return [mock_entry1]
        else:
            return [mock_entry2]

    with patch("aplist_gen.neighbor_scan.poll_neighbors", side_effect=side_effect_func):
        result = await poll_all_neighbors(["192.0.2.1", "192.0.2.2"], "public", "My Network")

        assert len(result) == 2
        bssids = {entry.bssid for entry in result}
        assert bssids == {"AA:BB:CC:DD:EE:FF", "BB:CC:DD:EE:FF:AA"}
