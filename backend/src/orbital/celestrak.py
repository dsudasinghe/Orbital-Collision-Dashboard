"""Polite CelesTrak GP client.

Follows https://celestrak.org/usage-policy.php: identify ourselves, cache every
download, and don't fetch the same query more than once per update interval.
"""

import gzip
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from orbital.config import Settings


class CelesTrakError(RuntimeError):
    pass


@dataclass
class GpFetch:
    records: list[dict[str, Any]]
    fetched_at: datetime
    from_cache: bool
    cache_path: Path


def _cache_dir(settings: Settings, group: str) -> Path:
    d = settings.data_dir / "celestrak" / group
    d.mkdir(parents=True, exist_ok=True)
    return d


def _latest_cached(cache_dir: Path) -> Path | None:
    files = sorted(cache_dir.glob("*.json.gz"))
    return files[-1] if files else None


def fetch_gp_group(settings: Settings, group: str, *, force: bool = False) -> GpFetch:
    """Fetch one CelesTrak GROUP as OMM JSON, reusing the cached copy if it is recent."""
    cache_dir = _cache_dir(settings, group)
    latest = _latest_cached(cache_dir)
    if latest and not force and time.time() - latest.stat().st_mtime < settings.celestrak_min_interval_s:
        with gzip.open(latest, "rt", encoding="utf-8") as f:
            records = json.load(f)
        fetched_at = datetime.fromtimestamp(latest.stat().st_mtime, UTC)
        return GpFetch(records, fetched_at, from_cache=True, cache_path=latest)

    resp = httpx.get(
        settings.celestrak_base_url,
        params={"GROUP": group, "FORMAT": "json"},
        headers={"User-Agent": settings.celestrak_user_agent},
        timeout=60,
        follow_redirects=True,
    )
    resp.raise_for_status()
    # CelesTrak answers some errors (unknown group, rate limit) with 200 and plain text.
    try:
        records = resp.json()
    except ValueError:
        raise CelesTrakError(f"non-JSON response for GROUP={group}: {resp.text[:200]!r}") from None
    if not isinstance(records, list):
        raise CelesTrakError(f"unexpected payload for GROUP={group}: {type(records).__name__}")

    fetched_at = datetime.now(UTC)
    path = cache_dir / f"{fetched_at:%Y%m%dT%H%M%SZ}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(resp.text)
    return GpFetch(records, fetched_at, from_cache=False, cache_path=path)
