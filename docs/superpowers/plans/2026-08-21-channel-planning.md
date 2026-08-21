# Channel Planning & Interference Avoidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dry-run `aplist-gen plan` command that recommends channel assignments for the 3-AP My Network cluster, balancing co-channel interference within the cluster and interference from external (neighbouring) Wi-Fi networks.

**Architecture:** A new pure-function `planner.py` scores candidate channels per AP (co-channel/adjacent-channel cost within the cluster, weighted external interference cost, a DFS penalty) and greedily assigns the lowest-cost channel to each AP in turn. External network data comes from a new `neighbor_scan.py` SNMP poller (re-using the RF neighbor-scan table `poller.py` currently avoids, but only for *external* ESSIDs) filtered through a small on-disk trust cache that requires a sighting to appear in more than one run before it counts. The CLI gets a `plan` subcommand alongside the existing default behaviour, which is unchanged.

**Tech Stack:** Python 3.12+, pysnmp (async SNMP), pytest/pytest-asyncio, uv for dependency management, Nomad + podman for deployment.

**Spec:** `docs/superpowers/specs/2026-08-21-channel-planning-design.md`

## Global Constraints

- Python >= 3.12 (per `pyproject.toml`); this environment has 3.13, compatible.
- New value types are `@dataclass(frozen=True)`, matching `core.py`'s existing style.
- No comments except where they explain a non-obvious WHY (matching the existing codebase's comment style) — never restate what code already says.
- File writes that must not corrupt existing state use the write-to-`.tmp`-then-`os.replace` pattern already established in `io_utils.write_output`.
- Per-host SNMP failures are logged as a warning and skipped, never fatal — matching `poll_all`'s existing `asyncio.gather(..., return_exceptions=True)` pattern.
- Run tests with `uv run pytest` from the repo root.

## Note on Task 6

Task 6 originally required live LAN access this sandbox didn't have. That access showed up mid-plan (2026-08-21) — `snmpwalk` is installed and the AP cluster is reachable — so Task 6's neighbor-scan OIDs have already been verified against real hardware (see Task 6 for the confirmed live output) and the task is now fully executable like the rest of the plan.

---

### Task 1: Extract shared SNMP helpers into `snmp_utils.py`

**Files:**
- Create: `src/aplist_gen/snmp_utils.py`
- Modify: `src/aplist_gen/poller.py`

**Interfaces:**
- Produces: `walk_bounded(engine, community: str, target, base_oid: str) -> dict[str, str]` (async), `format_bssid(value) -> str` — both used by `poller.py` (this task) and `neighbor_scan.py` (Task 6).

This is a pure refactor (move two helpers `poller.py` currently keeps private into a shared module) so both `poller.py` and the upcoming `neighbor_scan.py` can use them without duplicating SNMP-walk logic. No behavior change.

- [ ] **Step 1: Create `snmp_utils.py` with the extracted helpers**

```python
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    walk_cmd,
)


async def walk_bounded(engine, community: str, target, base_oid: str) -> dict[str, str]:
    """Walk base_oid, stopping as soon as a returned OID leaves that subtree.

    pysnmp's walk_cmd does not stop at a subtree boundary on its own --
    left unbounded it keeps walking the rest of the device's MIB.
    """
    results: dict[str, str] = {}
    async for error_indication, error_status, _error_index, var_binds in walk_cmd(
        engine,
        CommunityData(community),
        target,
        ContextData(),
        ObjectType(ObjectIdentity(base_oid)),
    ):
        if error_indication or error_status:
            break
        for var_bind in var_binds:
            oid_str = str(var_bind[0])
            if not oid_str.startswith(base_oid + "."):
                return results
            suffix = oid_str[len(base_oid) + 1 :]
            results[suffix] = var_bind[1]
    return results


def format_bssid(value) -> str:
    return ":".join(f"{b:02X}" for b in value.asOctets())
```

- [ ] **Step 2: Update `poller.py` to import from `snmp_utils` instead of defining its own copies**

Replace the top of `poller.py` (imports and the `_walk_bounded`/`_format_bssid` definitions) so it reads:

```python
from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget

from aplist_gen.core import APRecord, parse_own_radio_tables
from aplist_gen.snmp_utils import format_bssid, walk_bounded

BSSID_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.2"
ESSID_OID = "1.3.6.1.4.1.8744.5.4.1.5.1.25"
CHAN_5G_OID = "1.3.6.1.4.1.8744.5.4.3.9.1.1"
CHAN_24G_OID = "1.3.6.1.4.1.8744.5.4.3.14.1.3"


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
    raw_chan_5g = await walk_bounded(engine, community, target, CHAN_5G_OID)
    raw_chan_24g = await walk_bounded(engine, community, target, CHAN_24G_OID)

    bssids = {k: format_bssid(v) for k, v in raw_bssids.items()}
    essids = {k: v.prettyPrint() for k, v in raw_essids.items()}
    chan_5g = {k: v.prettyPrint() for k, v in raw_chan_5g.items()}
    chan_24g = {k: v.prettyPrint() for k, v in raw_chan_24g.items()}

    return parse_own_radio_tables(bssids, essids, chan_5g, chan_24g)
```

- [ ] **Step 3: Verify nothing broke**

Run: `uv run pytest -v`
Expected: all existing tests still PASS (this task changes no testable behavior, only where the code lives).

Also run: `uv run python -c "import aplist_gen.poller"` — expect no import error.

- [ ] **Step 4: Commit**

```bash
git add src/aplist_gen/snmp_utils.py src/aplist_gen/poller.py
git commit -m "refactor: extract shared SNMP walk helpers into snmp_utils.py"
```

---

### Task 2: Extend `ScanEntry` with an optional `rssi` field

**Files:**
- Modify: `src/aplist_gen/core.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces: `ScanEntry(bssid: str, essid: str, channel: int, rssi: int | None = None)` — used by Task 3 (`parse_neighbor_scan_table`), Task 5 (`planner.py`), Task 6 (`neighbor_scan.py`).

External sightings need a signal-strength weight for interference scoring; the cluster's own radios don't need this, so it's added as an optional field defaulting to `None` (existing `ScanEntry(...)` call sites without `rssi` keep working).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_core.py`:

```python
def test_scan_entry_rssi_defaults_to_none():
    entry = ScanEntry(bssid="02:00:00:00:00:01", essid="My Network", channel=132)

    assert entry.rssi is None


def test_scan_entry_accepts_rssi():
    entry = ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=44, rssi=-52)

    assert entry.rssi == -52
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_core.py -k scan_entry -v`
Expected: FAIL — `TypeError: ScanEntry.__init__() got an unexpected keyword argument 'rssi'` (for the second test) or `AttributeError` (for the first).

- [ ] **Step 3: Add the field**

In `src/aplist_gen/core.py`, change:

```python
@dataclass(frozen=True)
class ScanEntry:
    bssid: str
    essid: str
    channel: int
```

to:

```python
@dataclass(frozen=True)
class ScanEntry:
    bssid: str
    essid: str
    channel: int
    rssi: int | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_core.py -v`
Expected: PASS (including all pre-existing `test_core.py` tests — `rssi` defaulting to `None` doesn't change any existing `ScanEntry(...)` construction).

- [ ] **Step 5: Commit**

```bash
git add src/aplist_gen/core.py tests/test_core.py
git commit -m "feat: add optional rssi field to ScanEntry"
```

---

### Task 3: Add `parse_neighbor_scan_table` to `core.py`

**Files:**
- Modify: `src/aplist_gen/core.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `ScanEntry` from Task 2.
- Produces: `parse_neighbor_scan_table(bssids: dict[str, str], essids: dict[str, str], channels: dict[str, str], rssis: dict[str, str], own_essid: str) -> list[ScanEntry]` — used by Task 6 (`neighbor_scan.py`).

Pure parsing function (no SNMP/network), matching the existing split where `core.py` holds testable parsing logic and the poller module holds untested live-SNMP glue (`poller.py` / `parse_own_radio_tables` is the existing precedent). Takes four `row-suffix -> value` dicts (the shape every SNMP table walk in this codebase already produces) and returns only entries that are *not* the cluster's own ESSID, with a channel.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_core.py`:

```python
def test_parse_neighbor_scan_table_excludes_own_essid():
    bssids = {"1": "02:00:00:00:00:01"}
    essids = {"1": "My Network"}
    channels = {"1": "132"}
    rssis = {"1": "-50"}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == []


def test_parse_neighbor_scan_table_includes_external_with_rssi():
    bssids = {"1": "DE:AD:BE:EF:00:01"}
    essids = {"1": "NeighboursWifi"}
    channels = {"1": "44"}
    rssis = {"1": "-52"}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=44, rssi=-52)
    ]


def test_parse_neighbor_scan_table_defaults_missing_rssi_to_none():
    bssids = {"1": "DE:AD:BE:EF:00:01"}
    essids = {"1": "NeighboursWifi"}
    channels = {"1": "44"}
    rssis: dict[str, str] = {}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == [
        ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=44, rssi=None)
    ]


def test_parse_neighbor_scan_table_skips_row_with_no_channel():
    bssids = {"1": "DE:AD:BE:EF:00:01"}
    essids = {"1": "NeighboursWifi"}
    channels: dict[str, str] = {}
    rssis: dict[str, str] = {}

    entries = parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid="My Network")

    assert entries == []
```

Add `parse_neighbor_scan_table` to the existing `from aplist_gen.core import (...)` block at the top of `tests/test_core.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_core.py -k neighbor_scan_table -v`
Expected: FAIL with `ImportError: cannot import name 'parse_neighbor_scan_table'`.

- [ ] **Step 3: Implement it**

Add to `src/aplist_gen/core.py` (after `parse_own_radio_tables`):

```python
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
        channel = channels.get(suffix)
        if essid is None or essid == own_essid or channel is None:
            continue
        rssi_raw = rssis.get(suffix)
        rssi = int(rssi_raw) if rssi_raw is not None else None
        entries.append(
            ScanEntry(bssid=bssids[suffix], essid=essid, channel=int(channel), rssi=rssi)
        )
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_core.py -v`
Expected: PASS (all of `test_core.py`).

- [ ] **Step 5: Commit**

```bash
git add src/aplist_gen/core.py tests/test_core.py
git commit -m "feat: add parse_neighbor_scan_table for external network detection"
```

---

### Task 4: `trust_cache.py` — require repeat sightings before trusting an external network

**Files:**
- Create: `src/aplist_gen/trust_cache.py`
- Test: `tests/test_trust_cache.py`

**Interfaces:**
- Consumes: `ScanEntry` from Task 2.
- Produces: `load_cache(path: Path) -> dict[str, int]`, `save_cache(path: Path, cache: dict[str, int]) -> None`, `process_sightings(sightings: list[ScanEntry], cache: dict[str, int]) -> tuple[list[ScanEntry], dict[str, int]]` — used by Task 7 (CLI `plan` subcommand).

An external sighting only counts toward interference scoring once it has appeared in more than one `plan` run (per the design spec — the neighbor-scan table is known-unreliable, so single-poll blips shouldn't influence recommendations).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trust_cache.py`:

```python
from aplist_gen.core import ScanEntry
from aplist_gen.trust_cache import load_cache, process_sightings, save_cache


def test_load_cache_missing_file_returns_empty(tmp_path):
    assert load_cache(tmp_path / "missing.json") == {}


def test_load_cache_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("not json{{{")

    assert load_cache(path) == {}


def test_save_cache_then_load_round_trips(tmp_path):
    path = tmp_path / "cache.json"

    save_cache(path, {"AA:BB:CC:DD:EE:01": 2})

    assert load_cache(path) == {"AA:BB:CC:DD:EE:01": 2}


def test_save_cache_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "cache.json"

    save_cache(path, {"AA:BB:CC:DD:EE:01": 1})

    leftovers = [p for p in tmp_path.iterdir() if p != path]
    assert leftovers == []


def test_process_sightings_first_sighting_is_not_trusted():
    sighting = ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=6)

    trusted, updated_cache = process_sightings([sighting], cache={})

    assert trusted == []
    assert updated_cache == {"DE:AD:BE:EF:00:01": 1}


def test_process_sightings_repeated_sighting_is_trusted():
    sighting = ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=6)

    trusted, updated_cache = process_sightings([sighting], cache={"DE:AD:BE:EF:00:01": 1})

    assert trusted == [sighting]
    assert updated_cache == {"DE:AD:BE:EF:00:01": 2}


def test_process_sightings_drops_bssid_not_seen_this_run():
    trusted, updated_cache = process_sightings([], cache={"DE:AD:BE:EF:00:01": 3})

    assert trusted == []
    assert updated_cache == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trust_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aplist_gen.trust_cache'`.

- [ ] **Step 3: Implement it**

Create `src/aplist_gen/trust_cache.py`:

```python
import json
import os
from pathlib import Path

from aplist_gen.core import ScanEntry


def load_cache(path: Path) -> dict[str, int]:
    """Load BSSID -> seen-count. A missing or corrupt file is treated as
    a cold cache (empty), not an error -- this is a best-effort trust
    signal, not a source of truth.
    """
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: int(v) for k, v in raw.items() if isinstance(v, (int, float))}


def save_cache(path: Path, cache: dict[str, int]) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(cache))
    os.replace(tmp_path, path)


def process_sightings(
    sightings: list[ScanEntry], cache: dict[str, int]
) -> tuple[list[ScanEntry], dict[str, int]]:
    """Split sightings into (trusted, updated_cache).

    A sighting is trusted once its BSSID was already in the cache (seen
    in a previous run) and is seen again now -- i.e. it takes two poll
    runs before an external network counts toward interference scoring.
    """
    seen_bssids = {s.bssid for s in sightings}
    trusted_bssids = {b for b in seen_bssids if cache.get(b, 0) >= 1}
    updated_cache = {b: cache.get(b, 0) + 1 for b in seen_bssids}
    trusted = [s for s in sightings if s.bssid in trusted_bssids]
    return trusted, updated_cache
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_trust_cache.py -v`
Expected: PASS (all 7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aplist_gen/trust_cache.py tests/test_trust_cache.py
git commit -m "feat: add trust cache requiring repeat sightings for external networks"
```

---

### Task 5: `planner.py` — channel scoring & greedy assignment

**Files:**
- Create: `src/aplist_gen/planner.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: `APRecord` and `ScanEntry` from `core.py`.
- Produces: `ChannelRecommendation(bssid, essid, current_channel, recommended_channel, reason)`, `plan_channels(own_records: list[APRecord], trusted_external: list[ScanEntry], candidates_24g: list[int], candidates_5g: list[int], dfs_channels: set[int]) -> list[ChannelRecommendation]`, and constants `CANDIDATES_24G`, `CANDIDATES_5G`, `DFS_CHANNELS` — used by Task 7 (CLI `plan` subcommand).

This is the core algorithm from the spec: candidate channels are scored by co-channel/adjacent-channel overlap with other cluster APs plus signal-weighted overlap with trusted external sightings; DFS channels get a fixed penalty. Assignment is greedy, one AP at a time (sorted by BSSID for determinism), and an AP only moves off its current channel if a strictly cheaper candidate exists — a tie keeps the current channel, so a fully clear AP is never "recommended" to move for no reason.

**Regulatory note:** `CANDIDATES_5G`/`DFS_CHANNELS` below use the UNII-1 (36–48, non-DFS) and UNII-2/2e (52–140, DFS) channel sets, which are supported in effectively every regulatory domain including the UK/ETSI. UNII-3 (149–165) is deliberately excluded from the default candidate list since its legality/DFS status varies by regulatory domain and firmware — double check your APs' configured regulatory domain before widening this list.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_planner.py`:

```python
from aplist_gen.core import APRecord, ScanEntry
from aplist_gen.planner import CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS, plan_channels

NON_DFS_5G = [36, 40, 44, 48]


def test_plan_channels_keeps_ap_when_no_conflict():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6)]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert (recs[0].current_channel, recs[0].recommended_channel, recs[0].reason) == (6, 6, "")


def test_plan_channels_separates_conflicting_cluster_aps_on_24g():
    own_records = [
        APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6),
        APRecord(bssid="AA:AA:AA:AA:AA:02", essid="My Network", channel=6),
    ]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    first, second = recs
    assert (first.current_channel, first.recommended_channel) == (6, 6)
    assert second.current_channel == 6
    assert second.recommended_channel != 6
    assert "AA:AA:AA:AA:AA:01" in second.reason


def test_plan_channels_accounts_for_trusted_external_interference():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=6)]
    external = [ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=6, rssi=-40)]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    rec = recs[0]
    assert rec.current_channel == 6
    assert rec.recommended_channel != 6
    assert "NeighboursWifi" in rec.reason


def test_plan_channels_prefers_non_dfs_when_costs_tie():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=100)]
    external = [ScanEntry(bssid="DE:AD:BE:EF:00:01", essid="NeighboursWifi", channel=100, rssi=-40)]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert recs[0].recommended_channel in NON_DFS_5G


def test_plan_channels_uses_dfs_when_non_dfs_channels_are_all_congested():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=36)]
    external = [
        ScanEntry(bssid=f"DE:AD:BE:EF:00:0{i}", essid=f"Neighbour{i}", channel=ch, rssi=-40)
        for i, ch in enumerate(NON_DFS_5G, start=1)
    ]

    recs = plan_channels(own_records, external, CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert recs[0].recommended_channel in DFS_CHANNELS


def test_plan_channels_skips_records_with_unknown_channel():
    own_records = [APRecord(bssid="AA:AA:AA:AA:AA:01", essid="My Network", channel=None)]

    recs = plan_channels(own_records, [], CANDIDATES_24G, CANDIDATES_5G, DFS_CHANNELS)

    assert recs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aplist_gen.planner'`.

- [ ] **Step 3: Implement it**

Create `src/aplist_gen/planner.py`:

```python
from dataclasses import dataclass

from aplist_gen.core import APRecord, ScanEntry

CANDIDATES_24G: list[int] = [1, 6, 11]

# UNII-1: non-DFS on effectively every regulatory domain, including UK/ETSI.
NON_DFS_5G: list[int] = [36, 40, 44, 48]

# UNII-2/2e: DFS channels. Allowed as candidates but penalized in scoring
# (see DFS_PENALTY) -- coastal deployments see more weather/maritime radar,
# raising the odds of a forced radar-vacate on these channels.
DFS_CHANNELS: set[int] = {
    52, 56, 60, 64,
    100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140,
}

CANDIDATES_5G: list[int] = NON_DFS_5G + sorted(DFS_CHANNELS)

CO_CHANNEL_WEIGHT = 10.0
DFS_PENALTY = 15.0
UNKNOWN_SIGNAL_WEIGHT = 0.5


@dataclass(frozen=True)
class ChannelRecommendation:
    bssid: str
    essid: str
    current_channel: int
    recommended_channel: int
    reason: str


def band_of_channel(channel: int) -> str:
    return "24g" if channel <= 14 else "5g"


def _overlap_units(channel_a: int, channel_b: int, band: str) -> int:
    diff = abs(channel_a - channel_b)
    if band == "24g":
        return max(0, 5 - diff)
    return 5 if diff == 0 else 0


def cluster_overlap_cost(channel_a: int, channel_b: int, band: str) -> float:
    return _overlap_units(channel_a, channel_b, band) * CO_CHANNEL_WEIGHT


def _signal_weight(rssi: int | None) -> float:
    if rssi is None:
        return UNKNOWN_SIGNAL_WEIGHT
    return max(0.1, min(1.5, (rssi + 100) / 60))


def _candidate_cost(
    candidate: int,
    band: str,
    assigned: dict[str, int],
    trusted_external: list[ScanEntry],
    dfs_channels: set[int],
) -> float:
    cost = 0.0
    for other_channel in assigned.values():
        if band_of_channel(other_channel) != band:
            continue
        cost += cluster_overlap_cost(candidate, other_channel, band)
    for sighting in trusted_external:
        if band_of_channel(sighting.channel) != band:
            continue
        cost += cluster_overlap_cost(candidate, sighting.channel, band) * _signal_weight(sighting.rssi)
    if candidate in dfs_channels:
        cost += DFS_PENALTY
    return cost


def _contributors(
    channel: int,
    band: str,
    assigned: dict[str, int],
    trusted_external: list[ScanEntry],
) -> list[str]:
    contributors: list[str] = []
    for other_bssid, other_channel in assigned.items():
        if band_of_channel(other_channel) != band:
            continue
        if cluster_overlap_cost(channel, other_channel, band) > 0:
            contributors.append(f"cluster AP {other_bssid} on channel {other_channel}")
    for sighting in trusted_external:
        if band_of_channel(sighting.channel) != band:
            continue
        if cluster_overlap_cost(channel, sighting.channel, band) > 0:
            rssi_text = f" ({sighting.rssi}dBm)" if sighting.rssi is not None else ""
            contributors.append(
                f"external network '{sighting.essid}'{rssi_text} on channel {sighting.channel}"
            )
    return contributors


def _build_reason(
    current_channel: int,
    band: str,
    assigned: dict[str, int],
    trusted_external: list[ScanEntry],
) -> str:
    contributors = _contributors(current_channel, band, assigned, trusted_external)
    if not contributors:
        return "lower overall interference cost"
    return f"channel {current_channel} overlaps " + "; ".join(contributors)


def plan_channels(
    own_records: list[APRecord],
    trusted_external: list[ScanEntry],
    candidates_24g: list[int],
    candidates_5g: list[int],
    dfs_channels: set[int],
) -> list[ChannelRecommendation]:
    assigned: dict[str, int] = {}
    recommendations: list[ChannelRecommendation] = []

    for record in sorted(own_records, key=lambda r: r.bssid):
        if record.channel is None:
            continue
        band = band_of_channel(record.channel)
        candidates = candidates_24g if band == "24g" else candidates_5g

        current_cost = _candidate_cost(record.channel, band, assigned, trusted_external, dfs_channels)
        best_channel = record.channel
        best_cost = current_cost
        for candidate in candidates:
            if candidate == record.channel:
                continue
            cost = _candidate_cost(candidate, band, assigned, trusted_external, dfs_channels)
            if cost < best_cost:
                best_cost = cost
                best_channel = candidate

        if best_channel == record.channel:
            reason = ""
        else:
            reason = _build_reason(record.channel, band, assigned, trusted_external)

        assigned[record.bssid] = best_channel
        recommendations.append(
            ChannelRecommendation(
                bssid=record.bssid,
                essid=record.essid,
                current_channel=record.channel,
                recommended_channel=best_channel,
                reason=reason,
            )
        )

    return recommendations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_planner.py -v`
Expected: PASS (all 6 tests).

Then run the full suite: `uv run pytest -v`
Expected: PASS (everything, including Tasks 1–4's tests).

- [ ] **Step 5: Commit**

```bash
git add src/aplist_gen/planner.py tests/test_planner.py
git commit -m "feat: add channel scoring and greedy assignment planner"
```

---

### Task 6: Implement `neighbor_scan.py` with the confirmed OIDs

**Files:**
- Create: `src/aplist_gen/neighbor_scan.py`

**Interfaces:**
- Consumes: `walk_bounded`, `format_bssid` from `snmp_utils.py` (Task 1); `parse_neighbor_scan_table` from `core.py` (Task 3).
- Produces: `poll_neighbors(host: str, community: str, own_essid: str) -> list[ScanEntry]` (async), `poll_all_neighbors(hosts: list[str], community: str, own_essid: str) -> list[ScanEntry]` (async) — used by Task 7 (CLI `plan` subcommand).

The AP hardware is Colubris (rebadged as HP MSM), and the same `COLUBRIS-802DOT11-MIB` module that defines `poller.py`'s existing own-radio OIDs (all under `1.3.6.1.4.1.8744.5.4`) also defines a `coDot11ScanTable` — the RF neighbor-scan table needed here, with `MacAddress`/`Channel`/`SSID`/`SignalLevel` (units: dBm) columns, indexed the same `{ifIndex, scanIndex}` way as the existing association table. Its numeric OID was derived the same way `poller.py`'s existing constants derive from that MIB, then **confirmed live against 192.0.2.80 on 2026-08-21** (`snmpwalk -v2c -c public 192.0.2.80 1.3.6.1.4.1.8744.5.4.1.7`) — 110 rows came back across the cluster's radios, with real neighbouring-network data exactly matching the expected shape (e.g. `SSID: "EE WiFi"`, `SSID: "SKY27819"`, channel `36`, signal levels like `-83`, `-91`):

- BSSID (`coDot11ScanMacAddress`): `1.3.6.1.4.1.8744.5.4.1.7.1.2`
- Channel (`coDot11ScanChannel`): `1.3.6.1.4.1.8744.5.4.1.7.1.3`
- ESSID (`coDot11ScanSSID`): `1.3.6.1.4.1.8744.5.4.1.7.1.4`
- Signal level / RSSI, dBm (`coDot11ScanSignalLevel`): `1.3.6.1.4.1.8744.5.4.1.7.1.5`

- [ ] **Step 1: Implement `neighbor_scan.py` with the confirmed OIDs**

Create `src/aplist_gen/neighbor_scan.py`:

```python
import asyncio
import sys

from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget

from aplist_gen.core import ScanEntry, parse_neighbor_scan_table
from aplist_gen.snmp_utils import format_bssid, walk_bounded

# coDot11ScanTable from COLUBRIS-802DOT11-MIB, verified via live snmpwalk
# against an My Network HP MSM AP -- see Task 6 of
# docs/superpowers/plans/2026-08-21-channel-planning.md for how these
# were derived and verified.
NEIGHBOR_BSSID_OID = "1.3.6.1.4.1.8744.5.4.1.7.1.2"
NEIGHBOR_CHANNEL_OID = "1.3.6.1.4.1.8744.5.4.1.7.1.3"
NEIGHBOR_ESSID_OID = "1.3.6.1.4.1.8744.5.4.1.7.1.4"
NEIGHBOR_RSSI_OID = "1.3.6.1.4.1.8744.5.4.1.7.1.5"


async def poll_neighbors(host: str, community: str, own_essid: str, timeout: int = 3, retries: int = 1) -> list[ScanEntry]:
    """Poll one AP's RF neighbor-scan table for external (non-cluster) networks.

    This is the same table poller.poll_ap deliberately avoids for the
    cluster's own radio state (self-hearing was found unreliable on this
    hardware) -- here it's used only to detect other households' Wi-Fi,
    and results are further filtered through trust_cache before being
    trusted for scoring.
    """
    engine = SnmpEngine()
    target = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=retries)

    raw_bssids = await walk_bounded(engine, community, target, NEIGHBOR_BSSID_OID)
    raw_essids = await walk_bounded(engine, community, target, NEIGHBOR_ESSID_OID)
    raw_channels = await walk_bounded(engine, community, target, NEIGHBOR_CHANNEL_OID)
    raw_rssis = await walk_bounded(engine, community, target, NEIGHBOR_RSSI_OID)

    bssids = {k: format_bssid(v) for k, v in raw_bssids.items()}
    essids = {k: v.prettyPrint() for k, v in raw_essids.items()}
    channels = {k: v.prettyPrint() for k, v in raw_channels.items()}
    rssis = {k: v.prettyPrint() for k, v in raw_rssis.items()}

    return parse_neighbor_scan_table(bssids, essids, channels, rssis, own_essid=own_essid)


async def poll_all_neighbors(hosts: list[str], community: str, own_essid: str) -> list[ScanEntry]:
    results = await asyncio.gather(
        *(poll_neighbors(host, community, own_essid) for host in hosts), return_exceptions=True
    )

    entries: dict[str, ScanEntry] = {}
    for host, result in zip(hosts, results):
        if isinstance(result, Exception):
            print(f"warning: neighbor scan of {host} failed: {result}", file=sys.stderr)
            continue
        for entry in result:
            entries[entry.bssid] = entry
    return list(entries.values())
```

Add `import asyncio` and `import sys` to the top of the file alongside the `pysnmp`/`aplist_gen` imports.

- [ ] **Step 2: Confirm end-to-end against a live AP**

```bash
uv run python -c "
import asyncio
from aplist_gen.neighbor_scan import poll_neighbors
print(asyncio.run(poll_neighbors('192.0.2.80', 'public', 'My Network')))
"
```

Expected: a list of `ScanEntry` for nearby non-"My Network" networks (this cluster's live data has 100+ external sightings across its radios, so an empty list here would mean something's wrong, not that none are in range).

- [ ] **Step 3: Commit**

```bash
git add src/aplist_gen/neighbor_scan.py
git commit -m "feat: add live neighbor-scan polling for external network detection"
```

---

### Task 7: Wire up the `aplist-gen plan` CLI subcommand

**Files:**
- Modify: `src/aplist_gen/__init__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `poll_all` (existing), `poll_all_neighbors` (Task 6), `load_cache`/`save_cache`/`process_sightings` (Task 4), `plan_channels`/`CANDIDATES_24G`/`CANDIDATES_5G`/`DFS_CHANNELS` (Task 5).
- Produces: `parse_args` now accepts a `command` positional (`"generate"` default, or `"plan"`); `print_plan_report(recommendations: list[ChannelRecommendation]) -> None`; `run_plan(args) -> int`.

Adds a `command` positional argument (`generate` is the default, matching today's exact behavior with no args — so the existing Nomad job needs no changes) and a `run_plan` path that polls own + external state concurrently, updates the trust cache, plans, and prints a report. No file writes, no SNMP SET.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `parse_args([])` currently has no `.command` attribute, and `print_plan_report` doesn't exist yet.

- [ ] **Step 3: Implement it**

Replace the full contents of `src/aplist_gen/__init__.py` with:

```python
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


def run_plan(args: argparse.Namespace) -> int:
    hosts = [h.strip() for h in os.environ.get("AP_HOSTS", "").split(",") if h.strip()]
    community = os.environ.get("SNMP_COMMUNITY", "public")
    target_essid = os.environ.get("TARGET_ESSID", "My Network")
    cache_path = Path(os.environ.get("PLAN_CACHE_PATH", "plan_cache.json"))

    if not hosts:
        print("error: AP_HOSTS is not set (comma-separated list of AP IPs)", file=sys.stderr)
        return 1

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (all 5 tests).

Then run the full suite: `uv run pytest -v`
Expected: PASS (everything).

- [ ] **Step 5: Commit**

```bash
git add src/aplist_gen/__init__.py tests/test_cli.py
git commit -m "feat: add aplist-gen plan subcommand"
```

---

### Task 8: Nomad job for the planning batch job

**Files:**
- Create: `aplist-plan.nomad.hcl`

**Interfaces:**
- Consumes: `AP_HOSTS`, `TARGET_ESSID`, `SNMP_COMMUNITY`, `PLAN_CACHE_PATH` env vars read by `run_plan` (Task 7).

A separate, infrequent (hourly) batch job, decoupled from the existing 5-minute `aplist-gen` export job, per the design spec — a bug in this newer/less-proven code path can't affect the working exporter. Its trust cache needs to persist across runs, so it gets its own host volume, mirroring the existing job's TODO-style deployment notes for anything that needs confirming outside this repo.

- [ ] **Step 1: Create the job file**

```hcl
job "aplist-plan" {
  type = "batch"

  # Matches aplist-gen.nomad.hcl -- see its TODO note on confirming the
  # real datacenter name for the home Nomad cluster.
  datacenters = ["home"]

  periodic {
    cron             = "0 * * * *"
    prohibit_overlap = true
  }

  group "aplist-plan" {
    # TODO: create this host volume on the Nomad client before first
    # deploy. It holds only the small trust-cache JSON file (BSSID ->
    # seen-count) used to require repeat sightings before trusting an
    # external network -- not the served aplist.xml, which stays on
    # aplist-gen's separate "aplist-web" volume.
    volume "aplist-plan-cache" {
      type      = "host"
      read_only = false
      source    = "aplist-plan-cache"
    }

    task "aplist-plan" {
      driver = "podman"

      config {
        # Same image as aplist-gen -- see its TODO note on pinning a
        # real tag once the CI pipeline has built one.
        image   = "registry.gitlab.com/zandeez/aplist-gen:main"
        args    = ["plan"]
      }

      volume_mount {
        volume      = "aplist-plan-cache"
        destination = "/cache"
        read_only   = false
      }

      env {
        AP_HOSTS        = "192.0.2.80,192.0.2.149,192.0.2.78"
        TARGET_ESSID    = "My Network"
        PLAN_CACHE_PATH = "/cache/plan_cache.json"
      }

      # Same SNMP community variable as aplist-gen.
      template {
        data        = <<EOH
SNMP_COMMUNITY={{ with nomadVar "nomad/jobs/aplist-gen" }}{{ .community }}{{ end }}
EOH
        destination = "secrets/snmp.env"
        env         = true
      }

      resources {
        cpu    = 100
        memory = 64
      }
    }
  }
}
```

- [ ] **Step 2: Validate the job spec**

Run: `nomad job validate aplist-plan.nomad.hcl`
Expected: `Job validation successful` (matching what `nomad job validate aplist-gen.nomad.hcl` already produces on the existing file — a `cron is deprecated` warning is fine, the existing job file has the same warning).

- [ ] **Step 3: Commit**

```bash
git add aplist-plan.nomad.hcl
git commit -m "feat: add Nomad job for hourly channel-planning run"
```
