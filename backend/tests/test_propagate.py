import math
from datetime import timedelta

import pytest
from sgp4 import omm as sgp4_omm
from sgp4.api import Satrec

from orbital.omm import parse_records
from orbital.propagate import position_teme_km, to_satrec


def test_matches_sgp4_reference_initialiser(omm_sample):
    """Our unit conversions must match python-sgp4's own OMM loader for a normal ID."""
    (iss, *_), _ = parse_records(omm_sample)
    ref = Satrec()
    sgp4_omm.initialize(ref, {k: str(v) for k, v in omm_sample[0].items()})
    ours = to_satrec(iss)

    for minutes in (0, 90, 1440):
        when = iss.epoch + timedelta(minutes=minutes)
        _, r_ref, _ = ref.sgp4(ref.jdsatepoch, ref.jdsatepochF + minutes / 1440)
        r_ours = position_teme_km(ours, when)
        assert math.dist(r_ref, r_ours) < 1e-3  # under a metre


def test_sgp4_library_rejects_large_ids_directly(omm_sample):
    """Documents why to_satrec uses a placeholder satnum."""
    fields = {k: str(v) for k, v in omm_sample[2].items()}
    with pytest.raises(ValueError):
        sgp4_omm.initialize(Satrec(), fields)


@pytest.mark.parametrize("index", [1, 2], ids=["100000", "123456789"])
def test_large_catalog_numbers_propagate(omm_sample, index):
    recs, _ = parse_records(omm_sample)
    rec = recs[index]
    r = position_teme_km(to_satrec(rec), rec.epoch + timedelta(hours=1))
    altitude = math.hypot(*r) - 6378.137
    assert 300 < altitude < 1200  # plausible LEO
