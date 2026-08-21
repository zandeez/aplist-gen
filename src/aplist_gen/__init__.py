import argparse
import asyncio
import os
import sys
from pathlib import Path

from aplist_gen.core import APRecord, ScanEntry, to_simple_ap_list_text
from aplist_gen.io_utils import EmptyResultError, write_output
from aplist_gen.neighbor_scan import poll_all_neighbors
from aplist_gen.planner import CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS, ChannelRecommendation, plan_channels
from aplist_gen.poller import poll_ap
from aplist_gen.trust_cache import load_cache, process_sightings, save_cache


async def poll_all(hosts: list[str], community: str) -> list[APRecord]:
    results = await asyncio.gather(
        *(poll_ap(host, community) for host in hosts), return_exceptions=True
    )

    records_by_bssid: dict[str, APRecord] = {}
    for host, result in zip(hosts, results):
        if isinstance(result, Exception):
            print(f"warning: poll of {host} failed: {result}", file=sys.stderr)
            continue
        for record in result:
            records_by_bssid[record.bssid] = record
    return list(records_by_bssid.values())


async def _gather_plan_data(
    hosts: list[str], community: str, target_essid: str
) -> tuple[list[APRecord], list[ScanEntry]]:
    own_records, external_sightings = await asyncio.gather(
        poll_all(hosts, community),
        poll_all_neighbors(hosts, community, target_essid),
    )
    return own_records, external_sightings


def print_plan_report(recommendations: list[ChannelRecommendation]) -> None:
    for rec in recommendations:
        if rec.recommended_channel == rec.current_channel:
            print(f"{rec.bssid} {rec.essid!r}: channel {rec.current_channel} (no change)")
        else:
            print(
                f"{rec.bssid} {rec.essid!r}: channel {rec.current_channel} -> "
                f"{rec.recommended_channel} ({rec.reason})"
            )


def _read_ap_config() -> tuple[list[str], str, str] | None:
    hosts = [h.strip() for h in os.environ.get("AP_HOSTS", "").split(",") if h.strip()]
    community = os.environ.get("SNMP_COMMUNITY", "public")
    target_essid = os.environ.get("TARGET_ESSID", "My Network")

    if not hosts:
        print("error: AP_HOSTS is not set (comma-separated list of AP IPs)", file=sys.stderr)
        return None

    return hosts, community, target_essid


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate aplist.xml from live AP SNMP state")
    parser.add_argument(
        "command",
        nargs="?",
        default="generate",
        choices=["generate", "plan"],
        help="'generate' (default) writes aplist.xml; 'plan' reports channel "
        "recommendations without writing or changing anything",
    )
    parser.add_argument("--dry-run", action="store_true", help="print, don't write the output file (generate only)")
    parser.add_argument("--debug-dump", action="store_true", help="print per-host raw records")
    return parser.parse_args(argv)


def run_generate(args: argparse.Namespace) -> int:
    config = _read_ap_config()
    if config is None:
        return 1
    hosts, community, target_essid = config
    output_path = os.environ.get("OUTPUT_PATH", "aplist.xml")

    records = asyncio.run(poll_all(hosts, community))
    records = [r for r in records if r.essid == target_essid]

    if args.debug_dump:
        for record in records:
            print(record)

    text = to_simple_ap_list_text(records)

    if args.dry_run:
        print(text, end="")
        return 0

    try:
        write_output(Path(output_path), text, record_count=len(records))
    except EmptyResultError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        return 1

    return 0


def run_plan(args: argparse.Namespace) -> int:
    config = _read_ap_config()
    if config is None:
        return 1
    hosts, community, target_essid = config
    cache_path = Path(os.environ.get("PLAN_CACHE_PATH", "plan_cache.json"))

    own_records, external_sightings = asyncio.run(_gather_plan_data(hosts, community, target_essid))
    own_records = [r for r in own_records if r.essid == target_essid]

    cache = load_cache(cache_path)
    trusted, updated_cache = process_sightings(external_sightings, cache)
    save_cache(cache_path, updated_cache)

    if args.debug_dump:
        for sighting in trusted:
            print(sighting)

    recommendations = plan_channels(own_records, trusted, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)
    print_plan_report(recommendations)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "plan":
        return run_plan(args)
    return run_generate(args)


if __name__ == "__main__":
    raise SystemExit(main())
