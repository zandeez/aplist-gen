# Metrics Expansion + Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add six new SNMP-sourced per-radio metrics (SNR, noise floor, and four MSDU error/retry counters), one syslog-derived metric (config-change event count), and a native `vmui` dashboard — all additive to the existing `aplist-metrics` pipeline.

**Architecture:** Extend the existing own-radio poll (`poller.py`) with six more OID walks from the same MIB, thread the values through `core.py`'s `APRecord`/`parse_own_radio_tables`, emit them as new metric lines from `metrics.py`, add one new function that queries VictoriaLogs (already live) for a derived counter, wire it into `__init__.py`'s `run_metrics`, and add a static JSON dashboard to the already-deployed `victoria-metrics` job via a Nomad `template` block (no new host volume, no new job).

**Tech Stack:** Python (existing `aplist_gen` package, `uv`/`pytest`), pysnmp, Nomad/HCL, VictoriaLogs LogsQL.

**Spec:** `docs/superpowers/specs/2026-08-25-metrics-expansion-design.md`

## Global Constraints

- `APRecord` stays `@dataclass(frozen=True)` — no mutation.
- Minimal, WHY-only comments — this codebase's established convention. Don't explain WHAT the code does.
- `uv run pytest` must pass after every task.
- Zero changes to `run_generate`, `run_plan`, `poll_all`, `poll_all_neighbors`, `_gather_plan_data` — this work is additive/observability-only, same boundary as the original metrics plan.
- `poller.py` remains untested at the SNMP-glue level (matching this codebase's established, deliberate convention for network-touching code — see the original metrics plan's Task 3 ruling). Only pure-function transformation logic (in `core.py`, `metrics.py`) gets unit tests.
- SNMP indexing shapes are exact and must not be guessed or "simplified" — see each task's Interfaces section. A previous final review caught a real, live-verified bug from exactly this kind of mistake (`num_users` looked up by the wrong key shape) — every OID's index shape below was independently live-verified against `192.0.2.80` on 2026-08-25, not just derived from the `.my` file.
- Both `METRICS_URL` and `LOGS_URL` (new, Task 5) must point at the node's real LAN address (`192.0.2.1`), never `127.0.0.1` — `victoria-metrics`/`victoria-logs` both use Nomad's bridge/CNI networking, which only publishes on the advertised LAN address regardless of caller. This project already shipped and fixed exactly this bug once (`aplist-metrics.nomad.hcl`'s `METRICS_URL`, 2026-08-22 final review).
- No new host volumes, no new Nomad jobs. Only `core.py`, `poller.py`, `metrics.py`, `__init__.py`, `tests/test_core.py`, `tests/test_metrics.py`, `tests/test_cli.py`, and `victoria-metrics.nomad.hcl` are touched.

---

### Task 1: `core.py` — new `APRecord` fields + `parse_own_radio_tables` params

**Files:**
- Modify: `src/aplist_gen/core.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `APRecord` gains six new optional fields. `parse_own_radio_tables` gains six new optional dict params. Task 2 (`poller.py`) calls this with real SNMP data; Task 3 (`metrics.py`) reads the new `APRecord` fields.

**Exact index shapes** (this is the part that must not be guessed):
- `snr`: keyed by full `suffix` (e.g. `"4.1"`) — **same shape as `users`**, NOT `radio` alone. (`coDot11CurrentSNRLevel` lives in `coDot11AccessPointConfigTable`, same table/indexing as `coDot11BSSID`/`coDot11NumberOfUsers`.)
- `noise`, `retry_count`, `multiple_retry_count`, `failed_count`, `ack_failure_count`: all keyed by `radio` alone (e.g. `"4"`) — **same shape as `channels`**. (Their source tables — `coDot11CountersTable` is `ifIndex`-only-indexed; `noise` is pre-collapsed to radio-only by `poller.py` in Task 2, per that task's Interfaces section — `parse_own_radio_tables` itself never sees association-indexed data.)

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_own_radio_tables_includes_snr():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}
    snr = {"4.1": "35"}

    records = parse_own_radio_tables(bssids, essids, channels, snr=snr)

    assert records[0].snr_db == 35


def test_parse_own_radio_tables_snr_keyed_by_radio_only_does_not_match():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}
    snr = {"4": "35"}  # radio-only key -- wrong shape, must not match "4.1"

    records = parse_own_radio_tables(bssids, essids, channels, snr=snr)

    assert records[0].snr_db is None


def test_parse_own_radio_tables_includes_radio_keyed_counters():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}
    noise = {"4": "-86"}
    retry_count = {"4": "106268"}
    multiple_retry_count = {"4": "11018"}
    failed_count = {"4": "113060"}
    ack_failure_count = {"4": "113060"}

    records = parse_own_radio_tables(
        bssids, essids, channels,
        noise=noise,
        retry_count=retry_count,
        multiple_retry_count=multiple_retry_count,
        failed_count=failed_count,
        ack_failure_count=ack_failure_count,
    )

    assert records[0].noise_dbm == -86
    assert records[0].retry_count == 106268
    assert records[0].multiple_retry_count == 11018
    assert records[0].failed_count == 113060
    assert records[0].ack_failure_count == 113060


def test_parse_own_radio_tables_new_fields_default_to_none():
    bssids = {"4.1": "AA:AA:AA:AA:AA:01"}
    essids = {"4.1": "My Network"}
    channels = {"4": "100"}

    records = parse_own_radio_tables(bssids, essids, channels)

    assert records[0].snr_db is None
    assert records[0].noise_dbm is None
    assert records[0].retry_count is None
    assert records[0].multiple_retry_count is None
    assert records[0].failed_count is None
    assert records[0].ack_failure_count is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_core.py -v -k "snr or radio_keyed_counters or new_fields_default"`
Expected: FAIL (`TypeError: parse_own_radio_tables() got an unexpected keyword argument 'snr'` or `AttributeError`/similar for the missing `APRecord` fields).

- [ ] **Step 3: Implement**

In `src/aplist_gen/core.py`, extend `APRecord`:

```python
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
```

Extend `parse_own_radio_tables`:

```python
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
        channel = channels.get(radio)
        if channel is None:
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
                channel=int(channel),
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_core.py -v`
Expected: PASS (all tests, including pre-existing ones).

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/aplist_gen/core.py tests/test_core.py
git commit -m "feat: add SNR, noise, and error-counter fields to APRecord"
```

---

### Task 2: `poller.py` — six new OID walks

**Files:**
- Modify: `src/aplist_gen/poller.py`

**Interfaces:**
- Consumes: `parse_own_radio_tables`'s new `snr`/`noise`/`retry_count`/`multiple_retry_count`/`failed_count`/`ack_failure_count` params from Task 1.
- Produces: `poll_ap` returns `APRecord`s with the six new fields populated from live SNMP data. Nothing downstream in Task 3+ calls this directly (Task 3 works with hand-built `APRecord`s in tests), so this task has no test file — matches this codebase's established convention of leaving SNMP-glue code untested (see Global Constraints).

**Exact OIDs (all live-verified against `192.0.2.80` on 2026-08-25):**
- `SNR_OID = "1.3.6.1.4.1.8744.5.4.1.1.1.10"` (`coDot11CurrentSNRLevel`, same table/indexing as `BSSID_OID`/`USERS_OID` — `<radio>.<vap>`)
- `NOISE_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.5"` (`coDot11NoiseLevel`, `coDot11AssociationTable` — indexed `<radio>.<associationIndex>`, one row per currently-connected client, identical value across all of a radio's clients, absent entirely for a radio with zero clients)
- `RETRY_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.4"` (`coDot11RetryCount`, `coDot11CountersTable` — `<radio>` only)
- `MULTIPLE_RETRY_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.5"` (`coDot11MultipleRetryCount`, same table)
- `FAILED_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.3"` (`coDot11FailedCount`, same table)
- `ACK_FAILURE_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.9"` (`coDot11ACKFailureCount`, same table)

- [ ] **Step 1: Add the OID constants**

In `src/aplist_gen/poller.py`, after the existing `USERS_OID` constant:

```python
# coDot11CurrentSNRLevel -- same coDot11AccessPointConfigTable as
# coDot11BSSID above, same <radio>.<vap> indexing.
SNR_OID = "1.3.6.1.4.1.8744.5.4.1.1.1.10"

# coDot11NoiseLevel (coDot11AssociationTable) -- indexed by
# <radio>.<associationIndex>, i.e. one row per currently-connected
# client, NOT one per radio. Live-verified 2026-08-25: value is
# identical across every client of the same radio (it's an ambient
# noise-floor reading, not per-client), so poll_ap collapses this to
# one value per radio below. Absent entirely for a radio with zero
# connected clients -- same "just don't emit the metric" handling as
# num_users.
NOISE_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.5"

# coDot11CountersTable (coDot11mac.2) -- indexed by ifIndex (radio)
# alone, no VAP component. This hardware has exactly one VAP per
# radio (confirmed throughout this project), so attaching a
# radio-level counter to that radio's sole APRecord is unambiguous.
RETRY_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.4"
MULTIPLE_RETRY_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.5"
FAILED_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.3"
ACK_FAILURE_COUNT_OID = "1.3.6.1.4.1.8744.5.4.2.2.1.9"
```

- [ ] **Step 2: Walk the new OIDs and wire them into `poll_ap`**

Replace the body of `poll_ap` with:

```python
async def poll_ap(host: str, community: str, timeout: int = 3, retries: int = 1) -> list[APRecord]:
    """Poll one HP MSM AP's own radio config over SNMP.

    Targets the AP's own configured radios/VAPs directly (not the RF
    neighbor-scan table) since self-hearing over RF is not reliable --
    see memory note orb_ap_cluster_is_hp_msm.md for how this was derived.
    """
    engine = SnmpEngine()
    target = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=retries)

    raw_bssids = await walk_bounded(engine, community, target, BSSID_OID)
    raw_essids = await walk_bounded(engine, community, target, ESSID_OID)
    raw_channels = await walk_bounded(engine, community, target, CHANNEL_OID)
    raw_users = await walk_bounded(engine, community, target, USERS_OID)
    raw_snr = await walk_bounded(engine, community, target, SNR_OID)
    raw_noise = await walk_bounded(engine, community, target, NOISE_OID)
    raw_retry = await walk_bounded(engine, community, target, RETRY_COUNT_OID)
    raw_multiple_retry = await walk_bounded(engine, community, target, MULTIPLE_RETRY_COUNT_OID)
    raw_failed = await walk_bounded(engine, community, target, FAILED_COUNT_OID)
    raw_ack_failure = await walk_bounded(engine, community, target, ACK_FAILURE_COUNT_OID)

    bssids = {k: format_bssid(v) for k, v in raw_bssids.items()}
    essids = {k: v.prettyPrint() for k, v in raw_essids.items()}
    channels = {k: v.prettyPrint() for k, v in raw_channels.items()}
    users = {k: v.prettyPrint() for k, v in raw_users.items()}
    snr = {k: v.prettyPrint() for k, v in raw_snr.items()}
    retry_count = {k: v.prettyPrint() for k, v in raw_retry.items()}
    multiple_retry_count = {k: v.prettyPrint() for k, v in raw_multiple_retry.items()}
    failed_count = {k: v.prettyPrint() for k, v in raw_failed.items()}
    ack_failure_count = {k: v.prettyPrint() for k, v in raw_ack_failure.items()}

    # coDot11NoiseLevel is indexed per-client (<radio>.<associationIndex>);
    # collapse to one value per radio (identical across clients, see
    # NOISE_OID comment above) before it reaches parse_own_radio_tables,
    # which expects radio-only keys like channels/retry_count/etc.
    noise: dict[str, str] = {}
    for suffix, value in raw_noise.items():
        radio = suffix.split(".", 1)[0]
        noise.setdefault(radio, value.prettyPrint())

    return parse_own_radio_tables(
        bssids, essids, channels,
        host=host,
        users=users,
        snr=snr,
        noise=noise,
        retry_count=retry_count,
        multiple_retry_count=multiple_retry_count,
        failed_count=failed_count,
        ack_failure_count=ack_failure_count,
    )
```

- [ ] **Step 3: Run full suite**

Run: `uv run pytest -v`
Expected: PASS, no regressions (no new tests in this task, per the Interfaces note above).

- [ ] **Step 4: Commit**

```bash
git add src/aplist_gen/poller.py
git commit -m "feat: poll SNR, noise floor, and error counters per radio"
```

---

### Task 3: `metrics.py` — new metric lines in `format_own_radio_metrics`

**Files:**
- Modify: `src/aplist_gen/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `APRecord`'s six new fields from Task 1.
- Produces: six new metric names in the Prometheus-exposition text `format_own_radio_metrics` returns: `aplist_snr_db`, `aplist_noise_floor_dbm`, `aplist_retry_count`, `aplist_multiple_retry_count`, `aplist_failed_count`, `aplist_ack_failure_count`. Consumed by the dashboard (Task 6) and by whoever queries VictoriaMetrics directly — no other code in this repo reads these metric names.

- [ ] **Step 1: Write the failing tests**

```python
def test_format_own_radio_metrics_includes_snr_and_noise():
    records = [
        APRecord(
            bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78",
            snr_db=35, noise_dbm=-86,
        ),
    ]

    text = format_own_radio_metrics(records)

    assert 'aplist_snr_db{host="192.0.2.78",bssid="AA:AA:AA:AA:AA:01",essid="My Network"} 35' in text
    assert 'aplist_noise_floor_dbm{host="192.0.2.78",bssid="AA:AA:AA:AA:AA:01",essid="My Network"} -86' in text


def test_format_own_radio_metrics_includes_error_counters():
    records = [
        APRecord(
            bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78",
            retry_count=106268, multiple_retry_count=11018, failed_count=113060, ack_failure_count=113060,
        ),
    ]

    text = format_own_radio_metrics(records)

    labels = 'host="192.0.2.78",bssid="AA:AA:AA:AA:AA:01",essid="My Network"'
    assert f'aplist_retry_count{{{labels}}} 106268' in text
    assert f'aplist_multiple_retry_count{{{labels}}} 11018' in text
    assert f'aplist_failed_count{{{labels}}} 113060' in text
    assert f'aplist_ack_failure_count{{{labels}}} 113060' in text


def test_format_own_radio_metrics_omits_new_metrics_when_unknown():
    records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100, host="192.0.2.78"),
    ]

    text = format_own_radio_metrics(records)

    assert "aplist_snr_db" not in text
    assert "aplist_noise_floor_dbm" not in text
    assert "aplist_retry_count" not in text
    assert "aplist_multiple_retry_count" not in text
    assert "aplist_failed_count" not in text
    assert "aplist_ack_failure_count" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_metrics.py -v -k "snr_and_noise or error_counters or omits_new_metrics"`
Expected: FAIL (assertion errors — the new metric names aren't emitted yet).

- [ ] **Step 3: Implement**

Replace `format_own_radio_metrics` in `src/aplist_gen/metrics.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: PASS (all tests, including pre-existing ones).

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/aplist_gen/metrics.py tests/test_metrics.py
git commit -m "feat: emit SNR, noise, and error-counter metrics"
```

---

### Task 4: `metrics.py` — `query_config_change_count` (VictoriaLogs query)

**Files:**
- Modify: `src/aplist_gen/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing from earlier tasks in this plan.
- Produces: `query_config_change_count(logs_url: str, window: str = "15m") -> int`. Consumed by `__init__.py`'s `run_metrics` in Task 5.

**LogsQL query, live-verified 2026-08-25 against the real `victoria-logs` instance** (`_time:<duration>` relative-window filtering confirmed working — `_time:1m` returned 2 matches vs `_time:24h`'s 131, proving the filter is genuinely applied, not silently ignored):

```
eventmgr AND "Configuration has changed" AND _time:15m | stats count() as cnt
```

Response shape from VictoriaLogs (confirmed live): a single JSON line like `{"cnt":"7"}`, or an empty body if there are zero matches (VictoriaLogs sometimes returns nothing rather than `{"cnt":"0"}` for a fully-empty result set — handle both).

- [ ] **Step 1: Write the failing tests**

```python
def test_query_config_change_count_parses_response(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return b'{"cnt":"7"}\n'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["data"] = request.data
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    count = query_config_change_count("http://192.0.2.1:9428/select/logsql/query")

    assert count == 7
    assert captured["url"] == "http://192.0.2.1:9428/select/logsql/query"
    assert b"eventmgr" in captured["data"]
    assert b"_time:15m" in captured["data"]


def test_query_config_change_count_handles_empty_response(monkeypatch):
    class FakeResponse:
        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    count = query_config_change_count("http://192.0.2.1:9428/select/logsql/query")

    assert count == 0


def test_query_config_change_count_uses_custom_window(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return b'{"cnt":"1"}\n'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["data"] = request.data
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    query_config_change_count("http://192.0.2.1:9428/select/logsql/query", window="1h")

    assert b"_time:1h" in captured["data"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_metrics.py -v -k query_config_change_count`
Expected: FAIL (`AttributeError`/`ImportError` — `query_config_change_count` doesn't exist yet).

- [ ] **Step 3: Implement**

Add `import json` to the top of `src/aplist_gen/metrics.py` (alongside the existing `import urllib.request`). Then add:

```python
def query_config_change_count(logs_url: str, window: str = "15m") -> int:
    # Derived metric: counts "Configuration has changed" eventmgr lines
    # already flowing into victoria-logs, over a trailing window matching
    # aplist-metrics's own poll cadence. Not exact at window boundaries
    # (poll jitter can double-count or miss an event right at the edge) --
    # this is a coarse activity signal, not an audit log; the raw syslog
    # lines in victoria-logs remain authoritative if precision matters.
    query = f'eventmgr AND "Configuration has changed" AND _time:{window} | stats count() as cnt'
    request = urllib.request.Request(
        logs_url, data=f"query={query}".encode("utf-8"), method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8").strip()
    if not body:
        return 0
    return int(json.loads(body.splitlines()[0])["cnt"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: PASS (all tests, including pre-existing ones).

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/aplist_gen/metrics.py tests/test_metrics.py
git commit -m "feat: add VictoriaLogs-derived config-change-count query"
```

---

### Task 5: `__init__.py` — wire `LOGS_URL` into `run_metrics`

**Files:**
- Modify: `src/aplist_gen/__init__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `query_config_change_count` from Task 4.
- Produces: `run_metrics` pushes an additional `aplist_config_change_count <n>` line when `LOGS_URL` is set; the CLI's env-var contract (`LOGS_URL`, optional) is now part of this project's deployment surface, consumed by `aplist-metrics.nomad.hcl` (already deployed, no plan task touches that file for this — it's a manual `nomad var`/env change outside this repo's version-controlled job file, since `LOGS_URL`'s value is a fixed constant like `METRICS_URL`, not a secret needing Nomad-variable templating; **note for whoever deploys this task's result**: `aplist-metrics.nomad.hcl`'s `env` block needs `LOGS_URL = "http://192.0.2.1:9428/select/logsql/query"` added by hand after this task lands, mirroring how `METRICS_URL` is already set there).

**Current relevant code** (`src/aplist_gen/__init__.py`), for context — do not change `run_generate`/`run_plan`/`poll_all`/`_gather_plan_data`/`_poll_all_with_failures`/`_poll_all_neighbors_with_failures`/`_gather_metrics_data`, only `run_metrics`:

```python
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
```

- [ ] **Step 1: Write the failing tests**

```python
def test_run_metrics_appends_config_change_count_when_logs_url_set(monkeypatch, capsys):
    monkeypatch.setenv("AP_HOSTS", "192.0.2.80")
    monkeypatch.setenv("LOGS_URL", "http://192.0.2.1:9428/select/logsql/query")
    monkeypatch.delenv("METRICS_URL", raising=False)
    monkeypatch.setattr("aplist_gen._gather_metrics_data", lambda *a, **k: ([], [], []))
    monkeypatch.setattr("aplist_gen.query_config_change_count", lambda *a, **k: 4)

    exit_code = main(["metrics", "--debug-dump"])

    assert exit_code == 0
    assert "aplist_config_change_count 4" in capsys.readouterr().out


def test_run_metrics_skips_config_change_count_when_logs_url_unset(monkeypatch, capsys):
    monkeypatch.setenv("AP_HOSTS", "192.0.2.80")
    monkeypatch.delenv("LOGS_URL", raising=False)
    monkeypatch.delenv("METRICS_URL", raising=False)
    monkeypatch.setattr("aplist_gen._gather_metrics_data", lambda *a, **k: ([], [], []))

    exit_code = main(["metrics", "--debug-dump"])

    assert exit_code == 0
    assert "aplist_config_change_count" not in capsys.readouterr().out


def test_run_metrics_warns_and_continues_when_logs_query_fails(monkeypatch, capsys):
    monkeypatch.setenv("AP_HOSTS", "192.0.2.80")
    monkeypatch.setenv("LOGS_URL", "http://192.0.2.1:9428/select/logsql/query")
    monkeypatch.delenv("METRICS_URL", raising=False)
    monkeypatch.setattr("aplist_gen._gather_metrics_data", lambda *a, **k: ([], [], []))

    def raise_oserror(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("aplist_gen.query_config_change_count", raise_oserror)

    exit_code = main(["metrics", "--debug-dump"])

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "warning" in err
    assert "connection refused" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k config_change_count`
Expected: FAIL (`aplist_gen.query_config_change_count` not imported/found, or `aplist_config_change_count` never appears in output).

- [ ] **Step 3: Implement**

In `src/aplist_gen/__init__.py`, add `query_config_change_count` to the existing `from aplist_gen.metrics import (...)` block:

```python
from aplist_gen.metrics import (
    format_neighbor_metrics,
    format_own_radio_metrics,
    format_poll_failures,
    push_metrics,
    query_config_change_count,
)
```

Replace `run_metrics`:

```python
def run_metrics(args: argparse.Namespace) -> int:
    config = _read_ap_config()
    if config is None:
        return 1
    hosts, community, target_essid = config
    metrics_url = os.environ.get("METRICS_URL")
    logs_url = os.environ.get("LOGS_URL")

    own_records, external_sightings, failed_hosts = asyncio.run(
        _gather_metrics_data(hosts, community, target_essid)
    )
    own_records = [r for r in own_records if r.essid == target_essid]

    text = (
        format_own_radio_metrics(own_records)
        + format_neighbor_metrics(external_sightings)
        + format_poll_failures(hosts, failed_hosts)
    )

    if logs_url:
        try:
            config_change_count = query_config_change_count(logs_url)
            text += f"aplist_config_change_count {config_change_count}\n"
        except (OSError, ValueError, KeyError) as exc:
            print(f"warning: failed to query config-change count from {logs_url}: {exc}", file=sys.stderr)
    else:
        print("warning: LOGS_URL is not set, skipping config-change-count metric", file=sys.stderr)

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
```

(`ValueError`/`KeyError` are caught alongside `OSError` because `query_config_change_count` can raise either from malformed JSON or an unexpected response shape — a VictoriaLogs hiccup must not crash the whole metrics push, matching this function's Task 4 docstring/spec intent.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (all tests, including pre-existing ones).

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -v`
Expected: PASS, no regressions. Should be 78 + 4 (Task 1) + 3 (Task 3) + 3 (Task 4) + 3 (Task 5) = 91 tests total (exact count may differ slightly if an implementer's test count differs — treat "PASS, no regressions" as the actual bar, not the exact number).

- [ ] **Step 6: Commit**

```bash
git add src/aplist_gen/__init__.py tests/test_cli.py
git commit -m "feat: push config-change-count metric when LOGS_URL is set"
```

---

### Task 6: `victoria-metrics.nomad.hcl` — dashboard

**Files:**
- Modify: `victoria-metrics.nomad.hcl`

**Interfaces:**
- Consumes: the six new metric names from Task 3, plus the existing `aplist_own_channel`/`aplist_connected_users`/`aplist_external_network_count`/`aplist_poll_failure` metrics, plus `aplist_config_change_count` from Task 5.
- Produces: nothing consumed elsewhere in this repo — this is the final, human-facing task.

This task does not depend on Tasks 1-5 being deployed yet to validate (the dashboard JSON just references metric *names* as strings; `nomad job validate` doesn't check that referenced PromQL metric names actually exist). It can be implemented and validated independently, though the panels will show "no data" until Tasks 1-5's code is actually deployed and has pushed at least one data point.

- [ ] **Step 1: Add the dashboard JSON via a `template` block**

In `victoria-metrics.nomad.hcl`, inside the `task "victoria-metrics" { ... }` block, add a `template` block (Nomad auto-mounts `local/` into podman tasks, same mechanism `aplist-gen.nomad.hcl`'s `secrets/snmp.env` template already relies on — no `volume_mount` needed):

```hcl
      template {
        data        = <<EOH
{
  "title": "aplist",
  "rows": [
    {
      "panels": [
        { "title": "Own channel per radio", "width": 6, "expr": ["aplist_own_channel"] },
        { "title": "Connected users per radio", "width": 6, "expr": ["aplist_connected_users"] }
      ]
    },
    {
      "panels": [
        { "title": "SNR per radio (dB)", "width": 6, "expr": ["aplist_snr_db"] },
        { "title": "Noise floor per radio (dBm)", "width": 6, "expr": ["aplist_noise_floor_dbm"] }
      ]
    },
    {
      "panels": [
        {
          "title": "Retry/failure rate per radio",
          "width": 12,
          "expr": [
            "rate(aplist_retry_count[1h])",
            "rate(aplist_failed_count[1h])",
            "rate(aplist_ack_failure_count[1h])"
          ]
        }
      ]
    },
    {
      "panels": [
        { "title": "Busiest external channels", "width": 6, "expr": ["topk(10, aplist_external_network_count)"] },
        { "title": "Poll failures", "width": 6, "expr": ["aplist_poll_failure"] }
      ]
    },
    {
      "panels": [
        { "title": "Config-change events (per aplist-metrics poll)", "width": 12, "expr": ["aplist_config_change_count"] }
      ]
    }
  ]
}
EOH
        destination = "local/dashboards/aplist.json"
      }
```

- [ ] **Step 2: Point `vmui` at the dashboard directory**

In the same task's `config { args = [...] }` block, add the new flag:

```hcl
      config {
        image = "docker.io/victoriametrics/victoria-metrics:v1.102.0"
        ports = ["http"]
        args  = ["-storageDataPath=/storage", "-vmui.customDashboardsPath=/local/dashboards"]
      }
```

- [ ] **Step 3: Validate**

Run: `nomad job validate victoria-metrics.nomad.hcl`
Expected: `Job validation successful` (the existing `shutdown_delay` warning is expected and fine, matching every other service job in this repo).

- [ ] **Step 4: Commit**

```bash
git add victoria-metrics.nomad.hcl
git commit -m "feat: add vmui dashboard for aplist metrics"
```
