import argparse
import logging
import sys
from collections.abc import Callable

from sqlalchemy import text

from orbital import celestrak, ingest, satcat, spacetrack
from orbital.cache import CachedText
from orbital.config import get_settings
from orbital.db import get_engine
from orbital.omm import parse_records

log = logging.getLogger("orbital")

# A job fetches, parses and stores one feed: returns (download, received, inserted, rejected).
Job = Callable[[], tuple[CachedText, int, int, int]]


def _run_logged(source: str, job: Job) -> int:
    """Run a job with an ingest_runs record; the start is committed first so failures are still logged."""
    engine = get_engine()
    with engine.begin() as conn:
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
    return _run_logged(f"celestrak:{args.group}", gp_job(args.group, args.force))


def cmd_ingest_satcat(args: argparse.Namespace) -> int:
    return _run_logged("celestrak:satcat", satcat_job(args.force))


def cmd_ingest_cdm(args: argparse.Namespace) -> int:
    return _run_logged("spacetrack:cdm_public", cdm_job(args.days, args.force))


def cmd_refresh(args: argparse.Namespace) -> int:
    """Everything a scheduled run needs. Each feed's cache window decides whether it actually downloads."""
    status = _run_logged("celestrak:satcat", satcat_job(False))
    status |= _run_logged("celestrak:active", gp_job("active", False))
    if args.skip_cdm:
        return status
    settings = get_settings()
    if settings.spacetrack_identity and settings.spacetrack_password:
        status |= _run_logged("spacetrack:cdm_public", cdm_job(1, False))
    else:
        log.info("Space-Track credentials not set; skipping cdm_public")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orbital")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("db-check", help="test the database connection").set_defaults(func=cmd_db_check)

    p = sub.add_parser("ingest-gp", help="fetch a CelesTrak GP group as OMM JSON and store it")
    p.add_argument("--group", default="active", help="CelesTrak GROUP name (default: active)")
    p.add_argument("--force", action="store_true", help="ignore the 2-hour cache window")
    p.set_defaults(func=cmd_ingest_gp)

    p = sub.add_parser("ingest-satcat", help="fetch CelesTrak SATCAT and update object labels")
    p.add_argument("--force", action="store_true", help="ignore the 24-hour cache window")
    p.set_defaults(func=cmd_ingest_satcat)

    p = sub.add_parser("ingest-cdm", help="fetch Space-Track public conjunction messages")
    p.add_argument("--days", type=int, default=1, help="messages created in the last N days (default: 1)")
    p.add_argument("--force", action="store_true", help="ignore the 8-hour window (Space-Track allows 3/day)")
    p.set_defaults(func=cmd_ingest_cdm)

    p = sub.add_parser("refresh", help="scheduled run: SATCAT, GP and CDMs, each only when due")
    p.add_argument("--skip-cdm", action="store_true")
    p.set_defaults(func=cmd_refresh)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
