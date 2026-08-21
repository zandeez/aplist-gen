import argparse
import asyncio
import os
import sys
from pathlib import Path

from aplist_gen.core import APRecord, to_simple_ap_list_text
from aplist_gen.io_utils import EmptyResultError, write_output
from aplist_gen.poller import poll_ap


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate aplist.xml from live AP SNMP state")
    parser.add_argument("--dry-run", action="store_true", help="print, don't write the output file")
    parser.add_argument("--debug-dump", action="store_true", help="print per-host raw records")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    hosts = [h.strip() for h in os.environ.get("AP_HOSTS", "").split(",") if h.strip()]
    community = os.environ.get("SNMP_COMMUNITY", "public")
    output_path = os.environ.get("OUTPUT_PATH", "aplist.xml")
    target_essid = os.environ.get("TARGET_ESSID", "My Network")

    if not hosts:
        print("error: AP_HOSTS is not set (comma-separated list of AP IPs)", file=sys.stderr)
        return 1

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


if __name__ == "__main__":
    raise SystemExit(main())
