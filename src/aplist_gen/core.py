from dataclasses import dataclass


def band_of_channel(channel: int) -> str:
    return "24g" if channel <= 14 else "5g"


# HP 560 hardware fact (confirmed via the AP's own admin UI, 2026-08-25):
# Radio 1 is 5GHz-only, Radio 2 is 2.4GHz-only -- a chipset can't tune to
# the other band, so this is fixed, not something that changes at
# runtime. Used below to reject a handful of live-observed channel
# readings for a physically-impossible band on that radio (2026-08-25,
# 2026-08-27 -- e.g. the 2.4GHz radio briefly reporting channel 40 or
# 52), which always self-corrected next poll -- consistent with a
# transient SNMP read race (see walk_bounded's docstring for the
# suspected mechanism and fix) rather than a real AP-side fault.
RADIO_BAND = {"4": "5g", "5": "24g"}


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
    num_users: int | None = None
    snr_db: int | None = None
    noise_dbm: int | None = None
    retry_count: int | None = None
    multiple_retry_count: int | None = None
    failed_count: int | None = None
    ack_failure_count: int | None = None


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
    users: dict[str, str] | None = None,
    snr: dict[str, str] | None = None,
    noise: dict[str, str] | None = None,
    retry_count: dict[str, str] | None = None,
    multiple_retry_count: dict[str, str] | None = None,
    failed_count: dict[str, str] | None = None,
    ack_failure_count: dict[str, str] | None = None,
) -> list[APRecord]:
    users = users or {}
    snr = snr or {}
    noise = noise or {}
    retry_count = retry_count or {}
    multiple_retry_count = multiple_retry_count or {}
    failed_count = failed_count or {}
    ack_failure_count = ack_failure_count or {}
    records: list[APRecord] = []
    for suffix in sorted(bssids):
        radio = suffix.split(".", 1)[0]
        channel_raw = channels.get(radio)
        if channel_raw is None:
            continue
        channel = int(channel_raw)
        expected_band = RADIO_BAND.get(radio)
        if expected_band is not None and band_of_channel(channel) != expected_band:
            continue
        num_users_raw = users.get(suffix)
        num_users = int(num_users_raw) if num_users_raw is not None else None
        snr_raw = snr.get(suffix)
        snr_db = int(snr_raw) if snr_raw is not None else None
        noise_raw = noise.get(radio)
        noise_dbm = int(noise_raw) if noise_raw is not None else None
        retry_raw = retry_count.get(radio)
        retry = int(retry_raw) if retry_raw is not None else None
        multiple_retry_raw = multiple_retry_count.get(radio)
        multiple_retry = int(multiple_retry_raw) if multiple_retry_raw is not None else None
        failed_raw = failed_count.get(radio)
        failed = int(failed_raw) if failed_raw is not None else None
        ack_failure_raw = ack_failure_count.get(radio)
        ack_failure = int(ack_failure_raw) if ack_failure_raw is not None else None
        records.append(
            APRecord(
                bssid=bssids[suffix],
                essid=essids[suffix],
                channel=channel,
                host=host,
                num_users=num_users,
                snr_db=snr_db,
                noise_dbm=noise_dbm,
                retry_count=retry,
                multiple_retry_count=multiple_retry,
                failed_count=failed,
                ack_failure_count=ack_failure,
            )
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
