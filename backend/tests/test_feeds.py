from datetime import UTC, date, datetime

import pytest

from orbital import satcat, spacetrack
from orbital.cache import cached_download


def test_satcat_row_types_and_blanks():
    row = {
        "OBJECT_NAME": "FLOCK 4J-14", "OBJECT_ID": "2026-227DE", "NORAD_CAT_ID": "101357",
        "OBJECT_TYPE": "PAY", "OPS_STATUS_CODE": "+", "OWNER": "US", "LAUNCH_DATE": "2026-10-01",
        "LAUNCH_SITE": "AFWTR", "DECAY_DATE": "", "PERIOD": "94.90", "INCLINATION": "97.44",
        "APOGEE": "516", "PERIGEE": "511", "RCS": "", "DATA_STATUS_CODE": "",
        "ORBIT_CENTER": "EA", "ORBIT_TYPE": "ORB",
    }
    parsed = dict(zip(satcat.COLUMNS, satcat.parse_row(row)))
    assert parsed["norad_cat_id"] == 101357
    assert parsed["launch_date"] == date(2026, 10, 1)
    assert parsed["decay_date"] is None  # still in orbit
    assert parsed["rcs_m2"] is None
    assert parsed["apogee_km"] == 516.0


def test_satcat_bad_row_rejected():
    good, bad = satcat.parse_rows([{"NORAD_CAT_ID": "x"}])
    assert good == [] and bad[0][0] == "x"


def test_cdm_parse_handles_field_spellings():
    raw = {
        "CDM_ID": "1234567890", "CREATED": "2026-11-14 01:02:03.000000", "TCA": "2026-11-16T05:06:07.5",
        "MIN_RNG": "312", "PC": "1.2e-4", "EMERGENCY_REPORTABLE": "Y",
        "SAT_1_ID": "25544", "SAT_1_NAME": "ISS (ZARYA)", "SAT1_OBJECT_TYPE": "PAYLOAD",
        "SAT_2_ID": "123456789", "SAT_2_NAME": "DEB", "SAT2_OBJECT_TYPE": "DEBRIS",
    }
    parsed = dict(zip(spacetrack.COLUMNS, spacetrack.parse_cdm(raw)))
    assert parsed["cdm_id"] == 1234567890
    assert parsed["created"] == datetime(2026, 11, 14, 1, 2, 3, tzinfo=UTC)
    assert parsed["pc"] == pytest.approx(1.2e-4)
    assert parsed["emergency_reportable"] is True
    assert (parsed["sat1_id"], parsed["sat2_id"]) == (25544, 123456789)
    assert parsed["sat2_object_type"] == "DEBRIS"


def test_spacetrack_error_payload_rejected():
    with pytest.raises(spacetrack.SpaceTrackError):
        spacetrack._parse('{"error": "rate limit exceeded"}')


def test_cache_reuses_recent_download(tmp_path):
    calls = []
    def download():
        calls.append(1)
        return "[]"
    first = cached_download(tmp_path, "json", min_interval_s=3600, force=False, download=download)
    second = cached_download(tmp_path, "json", min_interval_s=3600, force=False, download=download)
    assert len(calls) == 1
    assert not first.from_cache and second.from_cache


def test_cache_never_stores_invalid_payload(tmp_path):
    def validate(text):
        raise ValueError("bad")
    with pytest.raises(ValueError):
        cached_download(tmp_path, "json", min_interval_s=3600, force=False,
                        download=lambda: "rate limited", validate=validate)
    assert list(tmp_path.iterdir()) == []
