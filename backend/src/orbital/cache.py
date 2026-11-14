"""Download-once cache for upstream feeds with usage limits.

Each download is kept gzipped under a timestamped name. A new download only
happens when the newest cached copy is older than the feed's minimum interval,
so reruns and retries never hit the upstream service twice.
"""

import gzip
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class CachedText:
    text: str
    fetched_at: datetime
    from_cache: bool
    path: Path


def cached_download(
    cache_dir: Path, ext: str, *, min_interval_s: int, force: bool, download: Callable[[], str],
    validate: Callable[[str], None] | None = None,
) -> CachedText:
    """`validate` should raise on an error payload so it is never cached as good data."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(cache_dir.glob(f"*.{ext}.gz"))
    latest = files[-1] if files else None
    if latest and not force and time.time() - latest.stat().st_mtime < min_interval_s:
        with gzip.open(latest, "rt", encoding="utf-8") as f:
            text = f.read()
        return CachedText(text, datetime.fromtimestamp(latest.stat().st_mtime, UTC), True, latest)

    text = download()
    if validate:
        validate(text)
    fetched_at = datetime.now(UTC)
    path = cache_dir / f"{fetched_at:%Y%m%dT%H%M%SZ}.{ext}.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    return CachedText(text, fetched_at, False, path)
