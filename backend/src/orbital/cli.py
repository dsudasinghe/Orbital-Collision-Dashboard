import argparse
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import text

from orbital import celestrak, conjunctions, ingest, satcat, spacetrack
from orbital.cache import CachedText
from orbital.config import get_settings
from orbital.db import get_engine
from orbital.omm import parse_records
from orbital.screening import ScreeningParams

log = logging.getLogger("orbital")

# A job fetches, parses and stores one feed: returns (download, received, inserted, rejected).
Job = Callable[[], tuple[CachedText, int, int, int]]

# Minimum time between upstream contacts per feed, from each provider's usage policy.
MIN_INTERVAL_S = {
    "celestrak:active": 2 * 3600,
    "celestrak:satcat": 24 * 3600,
    "spacetrack:gp": spacetrack.GP_MIN_INTERVAL_S,
    "spacetrack:cdm_public": spacetrack.CDM_MIN_INTERVAL_S,
}


def _run_logged(source: str, job: Job, *, force: bool = False) -> int:
    """Run a job with an ingest_runs record; the start is committed first so failures are still logged.

    Skips the job if the feed was contacted within its interval (checked in the
    database, so it holds on any machine). Failed attempts count too, so a bad
    login can't turn into a retry storm.
    """
    engine = get_engine()
    with engine.begin() as conn:
        age = ingest.last_attempt_age_s(conn, source)
        interval = MIN_INTERVAL_S.get(source, 0)
        if not force and age is not None and age < interval:
            print(f"{source}: not due (last contact {age / 3600:.1f} h ago, interval {interval / 3600:g} h)")
            return 0
        run_id = ingest.start_run(conn, source)
    try:
        fetch, received, inserted, rejected = job()
    except Exception as exc:
        with engine.begin() as conn:
            ingest.finish_run(conn, run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        log.exception("%s failed", source)
        return 1
    with engine.begin() as conn:
        ingest.finish_run(conn, run_id, status="ok", received=received, inserted=inserted,
                          rejected=rejected, from_cache=fetch.from_cache)
    print(
        f"{source}: {received} parsed, {inserted} new/updated, {rejected} rejected "
        f"({'cache' if fetch.from_cache else 'downloaded'} {fetch.fetched_at:%Y-%m-%d %H:%M}Z)"
    )
    return 0


def _log_rejected(rejected: list[tuple]) -> None:
    for key, err in rejected[:20]:
        log.warning("rejected %s: %s", key, err)


def gp_job(group: str, force: bool) -> Job:
    def job():
        raws, fetch = celestrak.fetch_gp_group(get_settings(), group, force=force)
        records, rejected = parse_records(raws)
        _log_rejected(rejected)
        with get_engine().begin() as conn:
            result = ingest.store_records(conn, records, f"celestrak:{group}")
        return fetch, result.received, result.inserted, len(rejected)
    return job


def satcat_job(force: bool) -> Job:
    def job():
        raws, fetch = celestrak.fetch_satcat(get_settings(), force=force)
        rows, rejected = satcat.parse_rows(raws)
        _log_rejected(rejected)
        with get_engine().begin() as conn:
            changed = satcat.store_rows(conn, rows)
        return fetch, len(rows), changed, len(rejected)
    return job


def spacetrack_gp_job(force: bool) -> Job:
    def job():
        raws, fetch = spacetrack.fetch_gp_catalog(get_settings(), force=force)
        records, rejected = parse_records(raws)
        _log_rejected(rejected)
        with get_engine().begin() as conn:
            result = ingest.store_records(conn, records, "spacetrack:gp")
        return fetch, result.received, result.inserted, len(rejected)
    return job


def cdm_job(days: int, force: bool) -> Job:
    def job():
        raws, fetch = spacetrack.fetch_cdm_public(get_settings(), days=days, force=force)
        rows, rejected = spacetrack.parse_cdms(raws)
        _log_rejected(rejected)
        with get_engine().begin() as conn:
            inserted = spacetrack.store_cdms(conn, rows)
        return fetch, len(rows), inserted, len(rejected)
    return job


def cmd_db_check(_args: argparse.Namespace) -> int:
    with get_engine().connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar_one()
    print(f"connected: {version}")
    return 0


def cmd_ingest_gp(args: argparse.Namespace) -> int:
    return _run_logged(f"celestrak:{args.group}", gp_job(args.group, args.force), force=args.force)


def cmd_ingest_satcat(args: argparse.Namespace) -> int:
    return _run_logged("celestrak:satcat", satcat_job(args.force), force=args.force)


def cmd_ingest_catalog(args: argparse.Namespace) -> int:
    return _run_logged("spacetrack:gp", spacetrack_gp_job(args.force), force=args.force)


def cmd_ingest_cdm(args: argparse.Namespace) -> int:
    return _run_logged("spacetrack:cdm_public", cdm_job(args.days, args.force), force=args.force)


def cmd_refresh(args: argparse.Namespace) -> int:
    """Everything a scheduled run needs; each feed runs only when its interval has passed.

    SATCAT goes first so new objects get labels before their element sets arrive.
    """
    status = _run_logged("celestrak:satcat", satcat_job(False))
    status |= _run_logged("celestrak:active", gp_job("active", False))
    settings = get_settings()
    if args.skip_spacetrack:
        return status
    if not (settings.spacetrack_identity and settings.spacetrack_password):
        log.info("Space-Track credentials not set; skipping gp and cdm_public")
        return status
    status |= _run_logged("spacetrack:gp", spacetrack_gp_job(False))
    # 2 days back: overlaps the 8-hour cadence so a missed run loses nothing.
    status |= _run_logged("spacetrack:cdm_public", cdm_job(2, False))
    return status


# Screening runs daily; skip if a run succeeded more recently than this.
SCREEN_MIN_INTERVAL_H = 20


def cmd_screen(args: argparse.Namespace) -> int:
    engine = get_engine()
    if not args.force and not args.ids:
        with engine.connect() as conn:
            age = conjunctions.last_ok_run_age_h(conn)
        if age is not None and age < SCREEN_MIN_INTERVAL_H:
            print(f"screening: not due (last run {age:.1f} h ago, interval {SCREEN_MIN_INTERVAL_H} h)")
            return 0
    start = datetime.now(UTC).replace(second=0, microsecond=0)
    params = ScreeningParams(start=start, hours=args.hours, step_s=args.step, threshold_km=args.threshold)
    try:
        result = conjunctions.run(engine, params, args.ids)
    except Exception:
        log.exception("screening failed")
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    with get_engine().begin() as conn:
        run_id = args.run or conn.execute(text("SELECT max(id) FROM screening_runs WHERE status = 'ok'")).scalar()
        if run_id is None:
            print("no successful screening runs yet")
            return 1
        print(json.dumps({"run_id": run_id, **conjunctions.revalidate(conn, run_id)}, indent=2))
    return 0


def cmd_watchlist(args: argparse.Namespace) -> int:
    with get_engine().begin() as conn:
        if args.action == "add":
            conn.execute(text("""
                INSERT INTO watchlist (norad_cat_id, label) VALUES (:id, :label)
                ON CONFLICT (norad_cat_id) DO UPDATE SET label = COALESCE(EXCLUDED.label, watchlist.label)
            """), {"id": args.id, "label": args.label})
        elif args.action == "remove":
            conn.execute(text("DELETE FROM watchlist WHERE norad_cat_id = :id"), {"id": args.id})
        for norad_id, label, name in conn.execute(text("""
            SELECT w.norad_cat_id, w.label, o.object_name FROM watchlist w JOIN objects o USING (norad_cat_id) ORDER BY 1
        """)):
            print(f"{norad_id:>9}  {label or name}")
        print(f"(plus objects in Space-Track public CDMs from the last {conjunctions.CDM_WATCH_DAYS} days, added at screening time)")
    return 0


def cmd_ml_risk(_args: argparse.Namespace) -> int:
    from orbital.ml import risk_model  # heavy imports only when used

    result = risk_model.evaluate()
    print(json.dumps({"rule": result["rule"], "cv": {k: v for k, v in result["cv"].items() if k != "rule_runners_up"},
                      "test": result["test"]}, indent=2))
    print(f"wrote {risk_model.MODEL_DIR / 'rule.json'} and results.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orbital")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("db-check", help="test the database connection").set_defaults(func=cmd_db_check)

    p = sub.add_parser("ingest-gp", help="fetch a CelesTrak GP group as OMM JSON and store it")
    p.add_argument("--group", default="active", help="CelesTrak GROUP name (default: active)")
    p.add_argument("--force", action="store_true", help="ignore the 2-hour interval")
    p.set_defaults(func=cmd_ingest_gp)

    p = sub.add_parser("ingest-satcat", help="fetch CelesTrak SATCAT and update object labels")
    p.add_argument("--force", action="store_true", help="ignore the 24-hour interval")
    p.set_defaults(func=cmd_ingest_satcat)

    p = sub.add_parser("ingest-catalog", help="fetch Space-Track's full GP catalog (debris included)")
    p.add_argument("--force", action="store_true", help="ignore the 8-hour window (Space-Track allows 1/hour)")
    p.set_defaults(func=cmd_ingest_catalog)

    p = sub.add_parser("ingest-cdm", help="fetch Space-Track public conjunction messages")
    p.add_argument("--days", type=int, default=1, help="messages created in the last N days (default: 1)")
    p.add_argument("--force", action="store_true", help="ignore the 8-hour window (Space-Track allows 3/day)")
    p.set_defaults(func=cmd_ingest_cdm)

    p = sub.add_parser("refresh", help="scheduled run: every feed whose interval has passed")
    p.add_argument("--skip-spacetrack", action="store_true")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("screen", help="screen the watchlist against the catalog and store conjunctions")
    p.add_argument("--hours", type=float, default=72, help="window length from now (default: 72)")
    p.add_argument("--step", type=float, default=30, help="coarse time step in seconds (default: 30)")
    p.add_argument("--threshold", type=float, default=10, help="report passes closer than this, km (default: 10)")
    p.add_argument("--ids", type=int, nargs="+", help="screen these catalog numbers instead of the watchlist")
    p.add_argument("--force", action="store_true", help=f"run even if a run succeeded in the last {SCREEN_MIN_INTERVAL_H} h")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("validate", help="compare a screening run with the Space-Track CDMs held now")
    p.add_argument("--run", type=int, help="screening run id (default: latest successful)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("watchlist", help="list, add or remove watchlist objects")
    p.add_argument("action", choices=["list", "add", "remove"], nargs="?", default="list")
    p.add_argument("id", type=int, nargs="?", help="catalog number (for add / remove)")
    p.add_argument("--label", help="display label (for add)")
    p.set_defaults(func=cmd_watchlist)

    sub.add_parser("ml-risk", help="fit and evaluate the collision-risk forecaster on ESA Kelvins data").set_defaults(func=cmd_ml_risk)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
