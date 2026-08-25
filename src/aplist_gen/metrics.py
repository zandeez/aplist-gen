import json
import urllib.parse
import urllib.request

from aplist_gen.core import APRecord, ScanEntry


def _band_of_channel(channel: int) -> str:
    return "24g" if channel <= 14 else "5g"


def format_own_radio_metrics(records: list[APRecord]) -> str:
    lines: list[str] = []
    for record in records:
        if record.channel is None:
            continue
        host = record.host or ""
        labels = f'host="{host}",bssid="{record.bssid}",essid="{record.essid}"'
        lines.append(f"aplist_own_channel{{{labels}}} {record.channel}")
        if record.num_users is not None:
            lines.append(f"aplist_connected_users{{{labels}}} {record.num_users}")
        if record.snr_db is not None:
            lines.append(f"aplist_snr_db{{{labels}}} {record.snr_db}")
        if record.noise_dbm is not None:
            lines.append(f"aplist_noise_floor_dbm{{{labels}}} {record.noise_dbm}")
        if record.retry_count is not None:
            lines.append(f"aplist_retry_count{{{labels}}} {record.retry_count}")
        if record.multiple_retry_count is not None:
            lines.append(f"aplist_multiple_retry_count{{{labels}}} {record.multiple_retry_count}")
        if record.failed_count is not None:
            lines.append(f"aplist_failed_count{{{labels}}} {record.failed_count}")
        if record.ack_failure_count is not None:
            lines.append(f"aplist_ack_failure_count{{{labels}}} {record.ack_failure_count}")
    return "".join(line + "\n" for line in lines)


def format_neighbor_metrics(sightings: list[ScanEntry]) -> str:
    counts: dict[int, int] = {}
    max_rssi: dict[int, int] = {}
    for sighting in sightings:
        counts[sighting.channel] = counts.get(sighting.channel, 0) + 1
        if sighting.rssi is not None:
            max_rssi[sighting.channel] = max(max_rssi.get(sighting.channel, sighting.rssi), sighting.rssi)

    lines: list[str] = []
    for channel in sorted(counts):
        band = _band_of_channel(channel)
        labels = f'channel="{channel}",band="{band}"'
        lines.append(f"aplist_external_network_count{{{labels}}} {counts[channel]}")
        if channel in max_rssi:
            lines.append(f"aplist_external_signal_dbm_max{{{labels}}} {max_rssi[channel]}")
    return "".join(line + "\n" for line in lines)


def format_poll_failures(hosts: list[str], failed_hosts: list[str]) -> str:
    failed = set(failed_hosts)
    lines = [f'aplist_poll_failure{{host="{host}"}} {1 if host in failed else 0}' for host in hosts]
    return "".join(line + "\n" for line in lines)


def push_metrics(url: str, text: str) -> None:
    request = urllib.request.Request(url, data=text.encode("utf-8"), method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def query_config_change_count(logs_url: str, window: str = "15m") -> int:
    # Derived metric: counts "Configuration has changed" eventmgr lines
    # already flowing into victoria-logs, over a trailing window matching
    # aplist-metrics's own poll cadence. Not exact at window boundaries
    # (poll jitter can double-count or miss an event right at the edge) --
    # this is a coarse activity signal, not an audit log; the raw syslog
    # lines in victoria-logs remain authoritative if precision matters.
    query = f'eventmgr AND "Configuration has changed" AND _time:{window} | stats count() as cnt'
    data = urllib.parse.urlencode({"query": query}).encode("utf-8")
    request = urllib.request.Request(logs_url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8").strip()
    if not body:
        return 0
    return int(json.loads(body.splitlines()[0])["cnt"])
