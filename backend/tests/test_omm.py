from datetime import UTC, datetime

from orbital.omm import parse_epoch, parse_records


def test_parses_all_sample_records(omm_sample):
    good, bad = parse_records(omm_sample)
    assert bad == []
    assert [r.norad_cat_id for r in good] == [25544, 100000, 123456789]


def test_catalog_ids_are_ints_even_when_served_as_strings(omm_sample):
    raw = dict(omm_sample[1], NORAD_CAT_ID="100000")
    (rec,), _ = parse_records([raw])
    assert rec.norad_cat_id == 100000
    assert isinstance(rec.norad_cat_id, int)


def test_epoch_is_utc():
    assert parse_epoch("2026-11-10T12:30:15.123456") == datetime(2026, 11, 10, 12, 30, 15, 123456, tzinfo=UTC)
    assert parse_epoch("2026-11-10T12:30:15Z").tzinfo == UTC


def test_bad_record_is_rejected_not_fatal(omm_sample):
    broken = dict(omm_sample[0])
    del broken["MEAN_MOTION"]
    good, bad = parse_records([broken, omm_sample[1]])
    assert [r.norad_cat_id for r in good] == [100000]
    assert bad[0][0] == 25544
