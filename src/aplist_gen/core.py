from dataclasses import dataclass


@dataclass(frozen=True)
class ScanEntry:
    bssid: str
    essid: str
    channel: int


@dataclass(frozen=True)
class APRecord:
    bssid: str
    essid: str
    channel: int | None


def filter_cluster_aps(entries: list[ScanEntry], target_essid: str) -> list[APRecord]:
    seen: dict[str, APRecord] = {}
    for entry in entries:
        if entry.essid != target_essid:
            continue
        seen[entry.bssid] = APRecord(bssid=entry.bssid, essid=entry.essid, channel=entry.channel)
    return list(seen.values())


def parse_own_radio_tables(
    bssids: dict[str, str],
    essids: dict[str, str],
    chan_5g: dict[str, str],
    chan_24g: dict[str, str],
) -> list[APRecord]:
    records: list[APRecord] = []
    for suffix in sorted(bssids):
        radio = suffix.split(".", 1)[0]
        channel = chan_5g.get(radio) or chan_24g.get(radio)
        if channel is None:
            continue
        records.append(
            APRecord(bssid=bssids[suffix], essid=essids[suffix], channel=int(channel))
        )
    return records


def to_simple_ap_list_text(records: list[APRecord]) -> str:
    lines = ["<?xml version='1.0'?>", "<simple-ap-list>", "# MAC SSID"]
    for record in records:
        line = f'{record.bssid} "{record.essid}"'
        # Channel is included whenever known; every record produced by the
        # live SNMP poll carries one, this branch only matters for
        # hand-built/test records.
        if record.channel is not None:
            line += f" {record.channel}"
        lines.append(line)
    lines.append("</simple-ap-list>")
    return "\n".join(lines) + "\n"


def write_aplist_xml(records: list[APRecord]) -> str:
    lines = ["<?xml version='1.0'?>", "<ap-list>"]
    for record in records:
        freq = "" if record.channel is None else str(record.channel)
        lines.append(f'  <ap mac="{record.bssid}" ssid="{record.essid}" freq="{freq}"/>')
    lines.append("</ap-list>")
    return "\n".join(lines) + "\n"
