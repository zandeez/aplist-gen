from dataclasses import dataclass


@dataclass(frozen=True)
class ScanEntry:
    bssid: str
    essid: str
    channel: int
    rssi: int | None = None


@dataclass(frozen=True)
class APRecord:
    bssid: str
    essid: str
    channel: int | None
    host: str | None = None


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
    channels: dict[str, str],
    host: str | None = None,
) -> list[APRecord]:
    records: list[APRecord] = []
    for suffix in sorted(bssids):
        radio = suffix.split(".", 1)[0]
        channel = channels.get(radio)
        if channel is None:
            continue
        records.append(
            APRecord(bssid=bssids[suffix], essid=essids[suffix], channel=int(channel), host=host)
        )
    return records


def parse_neighbor_scan_table(
    bssids: dict[str, str],
    essids: dict[str, str],
    channels: dict[str, str],
    rssis: dict[str, str],
    own_essid: str,
) -> list[ScanEntry]:
    entries: list[ScanEntry] = []
    for suffix in sorted(bssids):
        essid = essids.get(suffix)
        channel_raw = channels.get(suffix)
        if essid is None or essid == own_essid or channel_raw is None:
            continue
        try:
            channel = int(channel_raw)
        except ValueError:
            # This table is known-unreliable (~110 rows/host in production);
            # one row with a malformed channel shouldn't discard the rest.
            continue
        rssi_raw = rssis.get(suffix)
        try:
            rssi = int(rssi_raw) if rssi_raw is not None else None
        except ValueError:
            # rssi is optional data -- a malformed value is equivalent to
            # no value, same as a missing one.
            rssi = None
        entries.append(ScanEntry(bssid=bssids[suffix], essid=essid, channel=channel, rssi=rssi))
    return entries


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
