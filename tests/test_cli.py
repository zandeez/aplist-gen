from aplist_gen import _save_trust_cache_if_sightings, main, parse_args, print_plan_report
from aplist_gen.core import ScanEntry
from aplist_gen.planner import ChannelRecommendation


def test_parse_args_defaults_to_generate_command():
    args = parse_args([])

    assert args.command == "generate"


def test_parse_args_accepts_plan_command():
    args = parse_args(["plan"])

    assert args.command == "plan"


def test_parse_args_generate_still_accepts_dry_run():
    args = parse_args(["--dry-run"])

    assert args.command == "generate"
    assert args.dry_run is True


def test_print_plan_report_no_change(capsys):
    rec = ChannelRecommendation(
        bssid="AA:AA:AA:AA:AA:01",
        essid="My Network",
        current_channel=6,
        recommended_channel=6,
        reason="",
    )

    print_plan_report([rec])

    out = capsys.readouterr().out
    assert "channel 6 (no change)" in out


def test_print_plan_report_recommends_change(capsys):
    rec = ChannelRecommendation(
        bssid="AA:AA:AA:AA:AA:01",
        essid="My Network",
        current_channel=6,
        recommended_channel=1,
        reason="channel 6 overlaps cluster AP AA:AA:AA:AA:AA:02 on channel 6",
    )

    print_plan_report([rec])

    out = capsys.readouterr().out
    assert "6 -> 1" in out
    assert "overlaps cluster AP" in out


def test_print_plan_report_includes_host_when_known(capsys):
    rec = ChannelRecommendation(
        bssid="AA:AA:AA:AA:AA:01",
        essid="My Network",
        current_channel=6,
        recommended_channel=6,
        reason="",
        host="192.0.2.80",
    )

    print_plan_report([rec])

    out = capsys.readouterr().out
    assert "192.0.2.80" in out


def test_print_plan_report_omits_host_prefix_when_unknown(capsys):
    rec = ChannelRecommendation(
        bssid="AA:AA:AA:AA:AA:01",
        essid="My Network",
        current_channel=6,
        recommended_channel=6,
        reason="",
    )

    print_plan_report([rec])

    out = capsys.readouterr().out
    assert "None" not in out


def test_main_plan_without_ap_hosts_exits_1(monkeypatch, capsys):
    monkeypatch.delenv("AP_HOSTS", raising=False)

    exit_code = main(["plan"])

    assert exit_code == 1
    assert "AP_HOSTS is not set" in capsys.readouterr().err


def test_save_trust_cache_skips_write_when_no_sightings(tmp_path):
    # A fully-failed neighbor-scan poll (every host unreachable) must not
    # wipe out trust history built over prior runs.
    path = tmp_path / "missing_dir" / "cache.json"

    _save_trust_cache_if_sightings(path, external_sightings=[], updated_cache={"AA:BB:CC:DD:EE:01": 1})

    assert not path.exists()


def test_save_trust_cache_warns_instead_of_raising_on_write_failure(tmp_path, capsys):
    path = tmp_path / "missing_dir" / "cache.json"
    sighting = ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=6)

    _save_trust_cache_if_sightings(
        path, external_sightings=[sighting], updated_cache={"DE:AD:BE:EF:00:01": 1}
    )

    assert "warning" in capsys.readouterr().err
    assert not path.exists()


def test_save_trust_cache_writes_when_sightings_present(tmp_path):
    path = tmp_path / "cache.json"
    sighting = ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=6)

    _save_trust_cache_if_sightings(
        path, external_sightings=[sighting], updated_cache={"DE:AD:BE:EF:00:01": 1}
    )

    assert path.exists()
