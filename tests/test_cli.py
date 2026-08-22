from aplist_gen import (
    _save_trust_cache_if_sightings,
    _write_plan_report_if_configured,
    format_plan_report_html,
    main,
    parse_args,
    print_plan_report,
)
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


def test_format_plan_report_html_shows_no_change():
    rec = ChannelRecommendation(
        bssid="AA:AA:AA:AA:AA:01",
        essid="My Network",
        current_channel=6,
        recommended_channel=6,
        reason="",
        host="192.0.2.80",
    )

    html = format_plan_report_html([rec])

    assert "<!doctype html>" in html.lower()
    assert "192.0.2.80" in html
    assert "AA:AA:AA:AA:AA:01" in html
    assert "6 (no change)" in html


def test_format_plan_report_html_shows_recommended_change():
    rec = ChannelRecommendation(
        bssid="AA:AA:AA:AA:AA:01",
        essid="My Network",
        current_channel=6,
        recommended_channel=1,
        reason="channel 6 overlaps cluster AP AA:AA:AA:AA:AA:02 on channel 6",
        host="192.0.2.80",
    )

    html = format_plan_report_html([rec])

    assert "6 -&gt; 1" in html or "6 -> 1" in html
    assert "overlaps cluster AP" in html


def test_format_plan_report_html_escapes_untrusted_essid_and_reason():
    # ESSID and reason text can contain arbitrary bytes from other
    # people's Wi-Fi network names (the neighbor-scan table) -- this must
    # never be embedded into the HTML unescaped.
    rec = ChannelRecommendation(
        bssid="AA:AA:AA:AA:AA:01",
        essid="<script>alert(1)</script>",
        current_channel=6,
        recommended_channel=1,
        reason="channel 6 overlaps external network '<img src=x onerror=alert(1)>' on channel 6",
    )

    html = format_plan_report_html([rec])

    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


def test_write_plan_report_skips_when_path_not_configured():
    # None must not raise -- PLAN_REPORT_PATH is optional.
    _write_plan_report_if_configured(None, "<html></html>", record_count=1)


def test_write_plan_report_writes_when_path_configured(tmp_path):
    path = tmp_path / "plan_report.html"

    _write_plan_report_if_configured(str(path), "<html>hello</html>", record_count=1)

    assert path.read_text() == "<html>hello</html>"


def test_write_plan_report_refuses_empty_result_and_warns(tmp_path, capsys):
    path = tmp_path / "plan_report.html"
    path.write_text("<html>last-good</html>")

    _write_plan_report_if_configured(str(path), "<html></html>", record_count=0)

    assert "warning" in capsys.readouterr().err
    assert path.read_text() == "<html>last-good</html>"


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


def test_parse_args_accepts_metrics_command():
    args = parse_args(["metrics"])

    assert args.command == "metrics"


def test_main_metrics_without_ap_hosts_exits_1(monkeypatch, capsys):
    monkeypatch.delenv("AP_HOSTS", raising=False)

    exit_code = main(["metrics"])

    assert exit_code == 1
    assert "AP_HOSTS is not set" in capsys.readouterr().err
