import math
from datetime import timedelta

import numpy as np
import pytest

from orbital.frames import WGS84_A_KM, ecef_to_geodetic, gmst_rad, teme_to_ecef
from orbital.omm import parse_records
from orbital.propagate import position_teme_km, propagate_batch, to_satrec


def test_gmst_at_j2000():
    # Vallado: GMST at 2000-01-01 12:00 UT1 is 280.46061837 deg.
    assert math.degrees(gmst_rad(2451545.0)) == pytest.approx(280.46061837, abs=1e-6)


def test_gmst_advances_one_sidereal_turn_per_sidereal_day():
    sidereal_day = 0.99726958  # solar days
    a, b = gmst_rad(2460000.5), gmst_rad(2460000.5 + sidereal_day)
    assert math.degrees((b - a + math.pi) % (2 * math.pi) - math.pi) == pytest.approx(0, abs=1e-3)


@pytest.mark.parametrize("lat,lon,h", [(0, 0, 0), (0, 90, 400), (51.6, -60.4, 420), (89.9, 10, 800), (-45, 170, 35786)])
def test_geodetic_roundtrip(lat, lon, h):
    e2 = (1 / 298.257223563) * (2 - 1 / 298.257223563)
    la, lo = math.radians(lat), math.radians(lon)
    n = WGS84_A_KM / math.sqrt(1 - e2 * math.sin(la) ** 2)
    xyz = np.array([(n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo), (n * (1 - e2) + h) * math.sin(la)])
    got_lat, got_lon, got_h = ecef_to_geodetic(xyz)
    assert float(got_lat) == pytest.approx(lat, abs=1e-8)
    assert float(got_lon) == pytest.approx(lon, abs=1e-8)
    assert float(got_h) == pytest.approx(h, abs=1e-6)


def test_teme_to_ecef_preserves_length_and_z():
    r = np.array([[7000.0, -1200.0, 300.0]])
    out = teme_to_ecef(r, 1.234)
    assert np.linalg.norm(out) == pytest.approx(np.linalg.norm(r))
    assert out[0, 2] == r[0, 2]


def test_batch_matches_single_propagation(omm_sample):
    recs, _ = parse_records(omm_sample)
    sats = [to_satrec(r) for r in recs]
    t0 = recs[0].epoch
    times = [t0 + timedelta(minutes=m) for m in (0, 45, 600)]
    err, r, _ = propagate_batch(sats, times)
    assert err.shape == (3, 3) and r.shape == (3, 3, 3)
    assert not err.any()
    for i, sat in enumerate(sats):
        for j, t in enumerate(times):
            assert np.allclose(r[i, j], position_teme_km(sat, t), atol=1e-6)
