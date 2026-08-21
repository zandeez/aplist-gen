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
