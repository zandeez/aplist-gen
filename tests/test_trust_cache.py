import json

import pytest

from aplist_gen.core import ScanEntry
from aplist_gen.trust_cache import load_cache, process_sightings, save_cache


def test_load_cache_missing_file_returns_empty(tmp_path):
    assert load_cache(tmp_path / "missing.json") == {}


def test_load_cache_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("not json{{{")

    assert load_cache(path) == {}


def test_load_cache_valid_json_with_nan_returns_empty(tmp_path):
    # json.loads happily parses the non-standard "NaN" literal, but
    # int(float("nan")) raises ValueError -- a malformed cache like this
    # must degrade to a cold cache, not crash the plan command.
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"AA:BB:CC:DD:EE:01": float("nan")}))

    assert load_cache(path) == {}


def test_load_cache_path_is_a_directory_returns_empty(tmp_path):
    path = tmp_path / "cache.json"
    path.mkdir()

    assert load_cache(path) == {}


def test_save_cache_raises_oserror_for_nonexistent_directory(tmp_path):
    # save_cache itself does not swallow write failures -- run_plan is
    # responsible for catching this and treating it as a warning.
    path = tmp_path / "missing_dir" / "cache.json"

    with pytest.raises(OSError):
        save_cache(path, {"AA:BB:CC:DD:EE:01": 1})


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
