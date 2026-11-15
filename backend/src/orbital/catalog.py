"""Read the current catalog back out of the database."""

from dataclasses import dataclass

from sqlalchemy import Connection, text

from orbital.omm import OmmRecord

# Element sets older than this are too stale to propagate meaningfully
# (and usually mean the object has decayed or been lost).
MAX_AGE_DAYS = 30


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    elements: OmmRecord
    object_type: str | None  # PAY, R/B, DEB, UNK
    owner: str | None


def load_latest(conn: Connection, *, max_age_days: int = MAX_AGE_DAYS) -> list[CatalogEntry]:
    # Filtering on epoch inside the DISTINCT ON (rather than going through the
    # gp_latest view) lets Postgres prune old monthly partitions, so this stays
    # fast as history grows.
    rows = conn.execute(text("""
        SELECT g.*, o.object_name, o.object_id, o.object_type, o.owner
        FROM (
            SELECT DISTINCT ON (norad_cat_id) *
            FROM gp_elements
            WHERE epoch > now() - make_interval(days => :days)
            ORDER BY norad_cat_id, epoch DESC
        ) g
        JOIN objects o USING (norad_cat_id)
        WHERE o.decay_date IS NULL
        ORDER BY g.norad_cat_id
    """), {"days": max_age_days}).mappings()
    fields = OmmRecord.__slots__
    return [
        CatalogEntry(OmmRecord(**{f: r[f] for f in fields}), r["object_type"], r["owner"])
        for r in rows
    ]
