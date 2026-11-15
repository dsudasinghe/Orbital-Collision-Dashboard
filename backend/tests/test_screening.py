from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from orbital.catalog import CatalogEntry
from orbital.omm import OmmRecord
from orbital.propagate import position_teme_km, to_satrec
from orbital.screening import (
    Encounter,
    ScreeningParams,
    collision_probability,
    screen,
    shell_filter,
    shell_radii_km,
)

EPOCH = datetime(2026, 11, 15, 0, 0, tzinfo=UTC)


def circular(norad_id: int, inclination: float, mean_anomaly: float, mean_motion: float = 15.2) -> CatalogEntry:
    rec = OmmRecord(
        norad_cat_id=norad_id, object_name=f"T{norad_id}", object_id=None, epoch=EPOCH,
        mean_motion=mean_motion, eccentricity=0.0001, inclination=inclination, ra_of_asc_node=0.0,
        arg_of_pericenter=0.0, mean_anomaly=mean_anomaly, bstar=0.0, mean_motion_dot=0.0,
        mean_motion_ddot=0.0, ephemeris_type=0, classification_type="U", element_set_no=1, rev_at_epoch=1,
    )
    return CatalogEntry(rec, "PAY", None)


def brute_force_tca(a: CatalogEntry, b: CatalogEntry, start: datetime, minutes: int) -> tuple[datetime, float]:
    sa, sb = to_satrec(a.elements), to_satrec(b.elements)
    def dist(t: datetime) -> float:
        return float(np.linalg.norm(np.subtract(position_teme_km(sb, t), position_teme_km(sa, t))))

    # 1 s grid, then 1 ms around the best second (the pair closes at ~10 km/s).
    coarse = min((start + timedelta(seconds=k) for k in range(minutes * 60)), key=dist)
    fine = min((coarse + timedelta(milliseconds=k) for k in range(-1000, 1001)), key=dist)
    return fine, dist(fine)


@pytest.fixture
def crossing_pair():
    # Equatorial and polar circular orbits at the same altitude, both 10 degrees
    # before the shared node (RAAN 0), so they arrive there together after ~3 min.
    # J2 acts differently on the two inclinations, so they actually miss by ~14 km.
    return circular(1, 0.0, 350.0), circular(2, 90.0, 350.0)


def test_finds_crossing_at_brute_force_tca(crossing_pair):
    a, b = crossing_pair
    encounters, stats = screen([a, b], [1], ScreeningParams(start=EPOCH, hours=0.25, step_s=30, threshold_km=30))
    want_tca, want_miss = brute_force_tca(a, b, EPOCH, 15)
    assert stats.encounters == 1
    e = encounters[0]
    assert (e.sat1_id, e.sat2_id) == (1, 2)
    assert abs((e.tca - want_tca).total_seconds()) < 1.0
    assert e.miss_km == pytest.approx(want_miss, abs=0.05)
    assert e.rel_speed_kms > 10  # 90-degree crossing at ~7.6 km/s each
    assert np.linalg.norm([e.radial_km, e.in_track_km, e.cross_track_km]) == pytest.approx(e.miss_km, rel=1e-6)


def test_coarse_step_does_not_skip_fast_crossing(crossing_pair):
    # In 60 s the pair closes ~650 km, far more than the 30 km threshold.
    a, b = crossing_pair
    encounters, _ = screen([a, b], [1], ScreeningParams(start=EPOCH, hours=0.25, step_s=60, threshold_km=30))
    assert len(encounters) == 1


def test_shell_filter_drops_other_altitudes():
    low, same, high = circular(1, 51.6, 0.0), circular(2, 97.0, 0.0), circular(3, 51.6, 0.0, mean_motion=14.2)
    mm = np.array([e.elements.mean_motion for e in (low, same, high)])
    ecc = np.array([e.elements.eccentricity for e in (low, same, high)])
    rp, ra = shell_radii_km(mm, ecc)
    assert ra[2] - ra[0] > 300  # 14.2 rev/day is ~300 km higher than 15.2
    assert shell_filter(rp, ra, np.array([0]), 50.0).tolist() == [True, True, False]


def test_watch_pair_counted_once(crossing_pair):
    a, b = crossing_pair
    encounters, _ = screen([a, b], [1, 2], ScreeningParams(start=EPOCH, hours=0.25, step_s=30, threshold_km=30))
    assert len(encounters) == 1


def _encounter(miss_km: float) -> Encounter:
    # 90-degree crossing in LEO: sat2 offset radially by the miss distance, moving north.
    r1, v1 = (6800.0, 0.0, 0.0), (0.0, 7.6, 0.0)
    r2, v2 = (6800.0 + miss_km, 0.0, 0.0), (0.0, 0.0, 7.6)
    return Encounter(1, 2, EPOCH, miss_km, 10.7, miss_km, 0.0, 0.0, EPOCH, EPOCH, r1, v1, r2, v2)


def test_pc_decreases_with_miss_and_is_bounded():
    pcs = [collision_probability(_encounter(m), 5.0) for m in (0.0, 0.1, 0.3, 1.0)]
    assert all(0 <= p <= 1 for p in pcs)
    assert pcs == sorted(pcs, reverse=True)
    assert pcs[-1] < 1e-6 < pcs[0]


def test_older_elements_spread_probability():
    # Older elements widen in-track uncertainty, which lies in this crossing's
    # encounter plane, so the density at a small radial miss drops.
    fresh = _encounter(0.05)
    stale = replace(fresh, sat1_epoch=EPOCH - timedelta(days=5), sat2_epoch=EPOCH - timedelta(days=5))
    assert 0 < collision_probability(stale, 5.0) < collision_probability(fresh, 5.0)


def test_head_on_ignores_in_track_uncertainty():
    # Head-on, in-track lies along the relative velocity and projects out of the plane.
    head_on = replace(_encounter(0.2), v2=(0.0, -7.6, 0.0))
    stale = replace(head_on, sat1_epoch=EPOCH - timedelta(days=5), sat2_epoch=EPOCH - timedelta(days=5))
    assert collision_probability(stale, 5.0) == pytest.approx(collision_probability(head_on, 5.0))


def test_attached_objects_are_not_conjunctions():
    # Two catalog entries with identical elements: a module and its station.
    station, module = circular(1, 51.6, 10.0), circular(2, 51.6, 10.0)
    encounters, stats = screen([station, module], [1], ScreeningParams(start=EPOCH, hours=0.5, step_s=30, threshold_km=10))
    assert encounters == [] and stats.attached_skipped == 1


def test_slow_encounter_has_no_pc():
    slow = replace(_encounter(0.2), v2=(0.0, 7.65, 0.0))  # 50 m/s relative
    assert collision_probability(slow, 5.0) is None
