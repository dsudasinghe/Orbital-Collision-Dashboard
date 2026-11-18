from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from orbital.anomaly import ACTIVE, INERT, RuleDetector, can_manoeuvre, detect, events, history_features, semi_major_axis_km
from orbital.omm import OmmRecord

T0 = datetime(2026, 9, 1, tzinfo=UTC)
NDOT_HALF = 1e-5  # rev/day^2 (MEAN_MOTION_DOT), steady drag


def decaying_orbit(norad_id: int, burns: dict[int, float] | None = None, days: int = 40, seed: int = 0) -> list[OmmRecord]:
    """Element sets every 12 h for an orbit decaying under drag, with fit noise.

    `burns` maps a set index to a mean-motion change (rev/day) applied from
    that set on; negative raises the orbit.
    """
    rng = np.random.default_rng(seed)
    burns = burns or {}
    sets, offset = [], 0.0
    for k in range(days * 2):
        t = k * 0.5
        offset += burns.get(k, 0.0)
        n = 15.5 + 2 * NDOT_HALF * t + offset + rng.normal(0, 2e-6)
        sets.append(OmmRecord(
            norad_cat_id=norad_id, object_name=None, object_id=None, epoch=T0 + timedelta(days=t),
            mean_motion=n, eccentricity=0.0005 + rng.normal(0, 1e-6), inclination=51.6 + rng.normal(0, 1e-4),
            ra_of_asc_node=(100 - 5 * t) % 360, arg_of_pericenter=90.0, mean_anomaly=(360 * 15.5 * t) % 360,
            bstar=1e-4, mean_motion_dot=NDOT_HALF, mean_motion_ddot=0.0, ephemeris_type=0,
            classification_type="U", element_set_no=k, rev_at_epoch=k,
        ))
    return sets


def test_drag_is_not_a_residual():
    feats = history_features(decaying_orbit(1))
    assert feats.da_resid_km.abs().max() < 0.01  # only fit noise left


def test_reboost_is_one_orbit_raise_at_the_right_time():
    # -0.005 rev/day at 15.5 rev/day is about +1.5 km of semi-major axis.
    sets = decaying_orbit(1, burns={40: -0.005})
    flagged = RuleDetector().flag(history_features(sets))
    found = events(1, flagged)
    assert len(found) == 1
    m = found[0]
    assert m.kind == "orbit raise"
    assert m.epoch == sets[40].epoch
    expected = float(semi_major_axis_km(15.495) - semi_major_axis_km(15.5))  # ~ +1.5 km
    assert m.da_km == pytest.approx(expected, rel=0.05)


def test_quiet_object_has_no_flags():
    flagged = RuleDetector().flag(history_features(decaying_orbit(2, seed=3)))
    assert not flagged.flag.any()


def test_flags_within_merge_window_become_one_event():
    # A burn and a small follow-up fit adjustment one set later (as the ISS on 26 Sep 2026).
    sets = decaying_orbit(1, burns={40: -0.005, 41: -0.0004})
    found = events(1, RuleDetector().flag(history_features(sets)))
    assert len(found) == 1 and found[0].pairs_merged == 2


def test_detect_skips_objects_without_enough_history():
    manoeuvres, scores = detect({1: decaying_orbit(1, days=5), 2: decaying_orbit(2, burns={40: -0.005})})
    assert set(scores) == {2}
    assert [m.norad_cat_id for m in manoeuvres] == [2]


def test_merged_event_is_labelled_by_its_total_change():
    # A raise immediately undone by a bigger lowering nets to an orbit lower.
    sets = decaying_orbit(1, burns={40: -0.005, 41: 0.009})
    found = events(1, ACTIVE.flag(history_features(sets)), ACTIVE)
    assert len(found) == 1 and found[0].kind == "orbit lower" and found[0].da_km < 0


def test_objects_that_cannot_manoeuvre_get_anomalies_not_manoeuvres():
    burnt = decaying_orbit(7, burns={40: -0.005})
    manoeuvres, _ = detect({7: burnt}, active=set())
    assert [m.kind for m in manoeuvres] == ["anomaly"]


def test_inert_profile_is_stricter():
    # +0.15 km: a manoeuvre for an active payload, below the inert floor (0.3 km).
    sets = decaying_orbit(8, burns={40: -0.0005})
    assert len(events(8, ACTIVE.flag(history_features(sets)), ACTIVE)) == 1
    assert events(8, INERT.flag(history_features(sets)), INERT, inert=True) == []


def test_only_operational_payloads_can_manoeuvre():
    assert can_manoeuvre("PAY", "+") and can_manoeuvre("PAY", "P")
    assert not can_manoeuvre("PAY", "-") and not can_manoeuvre("DEB", None) and not can_manoeuvre("R/B", "+")
