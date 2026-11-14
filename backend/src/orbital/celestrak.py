"""Polite CelesTrak client for GP (OMM JSON) and SATCAT (CSV).

Follows https://celestrak.org/usage-policy.php: identify ourselves, cache every
download, and don't fetch the same query more than once per update interval.
"""

import csv
import io
import json
from typing import Any

import httpx

from orbital.cache import CachedText, cached_download
from orbital.config import Settings

SATCAT_URL = "https://celestrak.org/pub/satcat.csv"
_SATCAT_MIN_INTERVAL_S = 24 * 60 * 60  # SATCAT is regenerated once a day


class CelesTrakError(RuntimeError):
    pass


def _get(settings: Settings, url: str, params: dict[str, str] | None = None) -> str:
    resp = httpx.get(
        url, params=params, headers={"User-Agent": settings.celestrak_user_agent},
        timeout=120, follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


def _parse_gp(text: str) -> list[dict[str, Any]]:
    # CelesTrak answers some errors (unknown group, rate limit) with 200 and plain text.
    try:
        records = json.loads(text)
    except ValueError:
        raise CelesTrakError(f"non-JSON GP response: {text[:200]!r}") from None
    if not isinstance(records, list):
        raise CelesTrakError(f"unexpected GP payload: {type(records).__name__}")
    return records


def _parse_satcat(text: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "NORAD_CAT_ID" not in rows[0]:
        raise CelesTrakError(f"unexpected SATCAT payload: {text[:200]!r}")
    return rows


def fetch_gp_group(settings: Settings, group: str, *, force: bool = False) -> tuple[list[dict[str, Any]], CachedText]:
    """Fetch one CelesTrak GROUP as OMM JSON, reusing the cached copy if it is recent."""
    cached = cached_download(
        settings.data_dir / "celestrak" / group, "json",
        min_interval_s=settings.celestrak_min_interval_s, force=force,
        download=lambda: _get(settings, settings.celestrak_base_url, {"GROUP": group, "FORMAT": "json"}),
        validate=_parse_gp,
    )
    return _parse_gp(cached.text), cached


def fetch_satcat(settings: Settings, *, force: bool = False) -> tuple[list[dict[str, str]], CachedText]:
    """Full SATCAT (every catalogued object, on orbit or decayed) as CSV rows."""
    cached = cached_download(
        settings.data_dir / "celestrak" / "satcat", "csv",
        min_interval_s=_SATCAT_MIN_INTERVAL_S, force=force,
        download=lambda: _get(settings, SATCAT_URL),
        validate=_parse_satcat,
    )
    return _parse_satcat(cached.text), cached
