import argparse
import logging
import sys

from sqlalchemy import text

from orbital import celestrak, ingest
from orbital.config import get_settings
from orbital.db import get_engine
from orbital.omm import parse_records

log = logging.getLogger("orbital")


def cmd_db_check(_args: argparse.Namespace) -> int:
    with get_engine().connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar_one()
    print(f"connected: {version}")
    return 0


def cmd_ingest_gp(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = get_engine()
    source = f"celestrak:{args.group}"

    # The run record is committed on its own so a failed load is still logged.
    with engine.begin() as conn:
        run_id = ingest.start_run(conn, source)

    try:
        fetch = celestrak.fetch_gp_group(settings, args.group, force=args.force)
        records, rejected = parse_records(fetch.records)
        for cat_id, err in rejected[:20]:
            log.warning("rejected NORAD_CAT_ID=%s: %s", cat_id, err)
        with engine.begin() as conn:
            result = ingest.store_records(conn, records, source)
    except Exception as exc:
        with engine.begin() as conn:
            ingest.finish_run(conn, run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        log.exception("ingest failed")
        return 1

    with engine.begin() as conn:
        ingest.finish_run(
            conn, run_id, status="ok", received=result.received, inserted=result.inserted,
            rejected=len(rejected), from_cache=fetch.from_cache,
        )
    print(
        f"{source}: {result.received} parsed, {result.inserted} new element sets, "
        f"{len(rejected)} rejected ({'cache' if fetch.from_cache else 'downloaded'} {fetch.fetched_at:%Y-%m-%d %H:%M}Z)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orbital")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("db-check", help="test the database connection").set_defaults(func=cmd_db_check)

    p = sub.add_parser("ingest-gp", help="fetch a CelesTrak GP group as OMM JSON and store it")
    p.add_argument("--group", default="active", help="CelesTrak GROUP name (default: active)")
    p.add_argument("--force", action="store_true", help="ignore the 2-hour cache window")
    p.set_defaults(func=cmd_ingest_gp)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
