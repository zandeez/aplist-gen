from aplist_gen.core import ScanEntry
from aplist_gen.trust_cache import load_cache, process_sightings, save_cache


def test_load_cache_missing_file_returns_empty(tmp_path):
    assert load_cache(tmp_path / "missing.json") == {}


def test_load_cache_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("not json{{{")

    assert load_cache(path) == {}


def test_save_cache_then_load_round_trips(tmp_path):
    path = tmp_path / "cache.json"

    save_cache(path, {"AA:BB:CC:DD:EE:01": 2})

    assert load_cache(path) == {"AA:BB:CC:DD:EE:01": 2}


def test_save_cache_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "cache.json"

    save_cache(path, {"AA:BB:CC:DD:EE:01": 1})

    leftovers = [p for p in tmp_path.iterdir() if p != path]
    assert leftovers == []


def test_process_sightings_first_sighting_is_not_trusted():
    sighting = ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=6)

    trusted, updated_cache = process_sightings([sighting], cache={})

    assert trusted == []
    assert updated_cache == {"DE:AD:BE:EF:00:01": 1}


def test_process_sightings_repeated_sighting_is_trusted():
    sighting = ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=6)

    trusted, updated_cache = process_sightings([sighting], cache={"DE:AD:BE:EF:00:01": 1})

    assert trusted == [sighting]
    assert updated_cache == {"DE:AD:BE:EF:00:01": 2}


def test_process_sightings_drops_bssid_not_seen_this_run():
    trusted, updated_cache = process_sightings([], cache={"DE:AD:BE:EF:00:01": 3})

    assert trusted == []
    assert updated_cache == {}
