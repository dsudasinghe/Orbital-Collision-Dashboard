"""Compare our screening with Space-Track's public conjunction messages.

cdm_public holds several messages per event as Space-Track updates it; we
group them into events (same pair, TCAs within EVENT_GAP_S) and take the most
recently created message as the reference.
"""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import Connection, text

from orbital.screening import Encounter

EVENT_GAP_S = 300.0
MATCH_TOLERANCE_S = 120.0


@dataclass
class CdmEvent:
    sat1_id: int
    sat2_id: int
    tca: datetime
    miss_m: float
    pc: float | None
    messages: int


def load_cdm_events(conn: Connection, start: datetime, end: datetime) -> list[CdmEvent]:
    rows = conn.execute(text("""
        SELECT LEAST(sat1_id, sat2_id) AS a, GREATEST(sat1_id, sat2_id) AS b, tca, min_rng_m, pc, created
        FROM cdm_public WHERE tca BETWEEN :start AND :end
        ORDER BY a, b, tca
    """), {"start": start, "end": end}).all()
    events: list[CdmEvent] = []
    group: list = []

    def flush():
        if group:
            latest = max(group, key=lambda r: r.created)
            events.append(CdmEvent(latest.a, latest.b, latest.tca, latest.min_rng_m, latest.pc, len(group)))

    for r in rows:
        if group and ((r.a, r.b) != (group[-1].a, group[-1].b) or (r.tca - group[-1].tca).total_seconds() > EVENT_GAP_S):
            flush()
            group = []
        group.append(r)
    flush()
    return events


def match(events: list[CdmEvent], encounters: list[Encounter]) -> list[tuple[CdmEvent, Encounter | None]]:
    by_pair: dict[frozenset, list[Encounter]] = {}
    for e in encounters:
        by_pair.setdefault(frozenset((e.sat1_id, e.sat2_id)), []).append(e)
    out = []
    for ev in events:
        cands = [e for e in by_pair.get(frozenset((ev.sat1_id, ev.sat2_id)), [])
                 if abs((e.tca - ev.tca).total_seconds()) <= MATCH_TOLERANCE_S]
        out.append((ev, min(cands, key=lambda e: abs((e.tca - ev.tca).total_seconds())) if cands else None))
    return out


def summarise(pairs: list[tuple[CdmEvent, Encounter | None]], encounters: list[Encounter],
              screened_ids: set[int], catalog_ids: set[int]) -> dict:
    """Recall, accuracy and rank overlap.

    An event is in scope when we screened one of its objects and hold elements
    for both; events with analyst objects (no public elements) are reported but
    not counted against recall.
    """
    in_scope = [(ev, m) for ev, m in pairs
                if (ev.sat1_id in screened_ids or ev.sat2_id in screened_ids)
                and ev.sat1_id in catalog_ids and ev.sat2_id in catalog_ids]
    found = [(ev, m) for ev, m in in_scope if m]
    out: dict = {"cdm_events": len(pairs), "in_scope": len(in_scope), "matched": len(found)}
    if found:
        dt = np.array([abs((m.tca - ev.tca).total_seconds()) for ev, m in found])
        dm = np.array([abs(m.miss_km * 1000 - ev.miss_m) / 1000 for ev, m in found])
        out |= {
            "tca_diff_s_median": float(np.median(dt)),
            "miss_diff_km_median": float(np.median(dm)),
            "miss_diff_km_p90": float(np.percentile(dm, 90)),
        }
        ranked = sorted(encounters, key=lambda e: e.miss_km)
        keys = {(frozenset((m.sat1_id, m.sat2_id)), m.tca) for _, m in found}
        ranks = [k for k, e in enumerate(ranked) if (frozenset((e.sat1_id, e.sat2_id)), e.tca) in keys]
        top = min(100, len(ranked))
        out |= {
            "rank_by_miss_median": int(np.median(ranks)),
            f"events_in_our_top_{top}": sum(r < top for r in ranks),
        }
    return out
