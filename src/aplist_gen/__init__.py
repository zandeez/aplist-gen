import argparse
import asyncio
import html
import os
import sys
from pathlib import Path

from aplist_gen.core import APRecord, ScanEntry, to_simple_ap_list_text
from aplist_gen.io_utils import EmptyResultError, write_output
from aplist_gen.metrics import (
    format_neighbor_metrics,
    format_own_radio_metrics,
    format_poll_failures,
    push_metrics,
)
from aplist_gen.neighbor_scan import poll_all_neighbors, poll_neighbors
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


async def _poll_all_with_failures(hosts: list[str], community: str) -> tuple[list[APRecord], list[str]]:
    results = await asyncio.gather(
        *(poll_ap(host, community) for host in hosts), return_exceptions=True
    )
    records_by_bssid: dict[str, APRecord] = {}
    failed_hosts: list[str] = []
    for host, result in zip(hosts, results):
        if isinstance(result, Exception):
            print(f"warning: poll of {host} failed: {result}", file=sys.stderr)
            failed_hosts.append(host)
            continue
        for record in result:
            records_by_bssid[record.bssid] = record
    return list(records_by_bssid.values()), failed_hosts


async def _poll_all_neighbors_with_failures(
    hosts: list[str], community: str, own_essid: str
) -> tuple[list[ScanEntry], list[str]]:
    results = await asyncio.gather(
        *(poll_neighbors(host, community, own_essid) for host in hosts), return_exceptions=True
    )
    entries_by_bssid: dict[str, ScanEntry] = {}
    failed_hosts: list[str] = []
    for host, result in zip(hosts, results):
        if isinstance(result, Exception):
            print(f"warning: neighbor scan of {host} failed: {result}", file=sys.stderr)
            failed_hosts.append(host)
            continue
        for entry in result:
            entries_by_bssid[entry.bssid] = entry
    return list(entries_by_bssid.values()), failed_hosts


async def _gather_metrics_data(
    hosts: list[str], community: str, target_essid: str
) -> tuple[list[APRecord], list[ScanEntry], list[str]]:
    (own_records, own_failed), (external_sightings, external_failed) = await asyncio.gather(
        _poll_all_with_failures(hosts, community),
        _poll_all_neighbors_with_failures(hosts, community, target_essid),
    )
    failed_hosts = sorted(set(own_failed) | set(external_failed))
    return own_records, external_sightings, failed_hosts


def run_metrics(args: argparse.Namespace) -> int:
    config = _read_ap_config()
    if config is None:
        return 1
    hosts, community, target_essid = config
    metrics_url = os.environ.get("METRICS_URL")

    own_records, external_sightings, failed_hosts = asyncio.run(
        _gather_metrics_data(hosts, community, target_essid)
    )
    own_records = [r for r in own_records if r.essid == target_essid]

    text = (
        format_own_radio_metrics(own_records)
        + format_neighbor_metrics(external_sightings)
        + format_poll_failures(hosts, failed_hosts)
    )

    if args.debug_dump:
        print(text, end="")

    if not metrics_url:
        print("warning: METRICS_URL is not set, skipping push", file=sys.stderr)
        return 0

    try:
        push_metrics(metrics_url, text)
    except OSError as exc:
        print(f"warning: failed to push metrics to {metrics_url}: {exc}", file=sys.stderr)

    return 0


def print_plan_report(recommendations: list[ChannelRecommendation]) -> None:
    for rec in recommendations:
        prefix = f"{rec.host} " if rec.host is not None else ""
        if rec.recommended_channel == rec.current_channel:
            print(f"{prefix}{rec.bssid} {rec.essid!r}: channel {rec.current_channel} (no change)")
        else:
            print(
                f"{prefix}{rec.bssid} {rec.essid!r}: channel {rec.current_channel} -> "
                f"{rec.recommended_channel} ({rec.reason})"
            )


def format_plan_report_html(recommendations: list[ChannelRecommendation]) -> str:
    rows = []
    for rec in recommendations:
        if rec.recommended_channel == rec.current_channel:
            change = f"{rec.current_channel} (no change)"
            reason = ""
        else:
            change = f"{rec.current_channel} -&gt; {rec.recommended_channel}"
            reason = rec.reason
        rows.append(
            "<tr>"
            f"<td>{html.escape(rec.host or '')}</td>"
            f"<td>{html.escape(rec.bssid)}</td>"
            f"<td>{html.escape(rec.essid)}</td>"
            f"<td>{change}</td>"
            f"<td>{html.escape(reason)}</td>"
            "</tr>"
        )
    table_rows = "\n".join(rows)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>aplist-gen channel plan</title>
<style>
  body {{ font-family: sans-serif; margin: 2em; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4em 0.6em; text-align: left; vertical-align: top; }}
  th {{ background: #f0f0f0; }}
</style>
</head>
<body>
<h1>Channel plan</h1>
<table>
<tr><th>Host</th><th>BSSID</th><th>ESSID</th><th>Channel</th><th>Reason</th></tr>
{table_rows}
</table>
</body>
</html>
"""


def _write_plan_report_if_configured(report_path: str | None, text: str, record_count: int) -> None:
    if not report_path:
        return
    try:
        write_output(Path(report_path), text, record_count=record_count)
    except EmptyResultError as exc:
        print(f"warning: {exc}", file=sys.stderr)


def _save_trust_cache_if_sightings(
    cache_path: Path, external_sightings: list[ScanEntry], updated_cache: dict[str, int]
) -> None:
    """Persist the trust cache, but skip entirely on a fully-failed poll.

    If every host's neighbor-scan poll failed this run, external_sightings
    is empty and updated_cache would just be {} -- writing that would wipe
    out trust history built over prior runs for no good reason. A write
    failure (e.g. the cache directory doesn't exist yet) is a warning, not
    fatal -- the plan report must still print either way.
    """
    if not external_sightings:
        return
    try:
        save_cache(cache_path, updated_cache)
    except OSError as exc:
        print(f"warning: failed to write trust cache {cache_path}: {exc}", file=sys.stderr)


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
        choices=["generate", "plan", "metrics"],
        help="'generate' (default) writes aplist.xml; 'plan' reports channel "
        "recommendations without writing or changing anything; 'metrics' "
        "pushes observational data to VictoriaMetrics",
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
    _save_trust_cache_if_sightings(cache_path, external_sightings, updated_cache)

    if args.debug_dump:
        for sighting in trusted:
            print(sighting)

    recommendations = plan_channels(own_records, trusted, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)
    print_plan_report(recommendations)

    report_path = os.environ.get("PLAN_REPORT_PATH")
    _write_plan_report_if_configured(
        report_path, format_plan_report_html(recommendations), record_count=len(recommendations)
    )

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "plan":
        return run_plan(args)
    if args.command == "metrics":
        return run_metrics(args)
    return run_generate(args)


if __name__ == "__main__":
    raise SystemExit(main())
