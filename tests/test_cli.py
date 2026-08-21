from aplist_gen import parse_args, print_plan_report
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
