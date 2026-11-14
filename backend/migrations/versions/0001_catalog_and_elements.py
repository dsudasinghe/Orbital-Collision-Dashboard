"""Catalog objects, GP element history, ingest run log.

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # integer, not a five-digit string: catalog numbers passed 99999 in July 2026
    # and will reach nine digits (integer holds up to 2,147,483,647).
    op.execute("""
        CREATE TABLE objects (
            norad_cat_id  integer PRIMARY KEY CHECK (norad_cat_id > 0),
            object_name   text,
            object_id     text,
            first_seen    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
    """)

    # Every element set ever seen. Partitioned by month of epoch because history
    # grows by ~10k+ rows per refresh; partitions are created on demand by ingest.
    op.execute("""
        CREATE TABLE gp_elements (
            norad_cat_id        integer NOT NULL REFERENCES objects (norad_cat_id),
            epoch               timestamptz NOT NULL,
            mean_motion         double precision NOT NULL,
            eccentricity        double precision NOT NULL,
            inclination         double precision NOT NULL,
            ra_of_asc_node      double precision NOT NULL,
            arg_of_pericenter   double precision NOT NULL,
            mean_anomaly        double precision NOT NULL,
            bstar               double precision NOT NULL,
            mean_motion_dot     double precision NOT NULL,
            mean_motion_ddot    double precision NOT NULL,
            ephemeris_type      smallint NOT NULL,
            classification_type char(1) NOT NULL,
            element_set_no      integer,
            rev_at_epoch        integer,
            source              text NOT NULL,
            fetched_at          timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (norad_cat_id, epoch)
        ) PARTITION BY RANGE (epoch)
    """)
    op.execute("CREATE INDEX gp_elements_epoch_idx ON gp_elements (epoch)")

    # Latest set per object: what propagation and the globe read.
    # security_invoker so the view can't be used to read around RLS via Supabase's API.
    op.execute("""
        CREATE VIEW gp_latest WITH (security_invoker = true) AS
        SELECT DISTINCT ON (norad_cat_id) *
        FROM gp_elements
        ORDER BY norad_cat_id, epoch DESC
    """)

    # One row per scheduled run; the "two weeks unattended" check reads this.
    op.execute("""
        CREATE TABLE ingest_runs (
            id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source         text NOT NULL,
            started_at     timestamptz NOT NULL DEFAULT now(),
            finished_at    timestamptz,
            status         text NOT NULL DEFAULT 'running'
                           CHECK (status IN ('running', 'ok', 'failed')),
            rows_received  integer,
            rows_inserted  integer,
            rows_rejected  integer,
            from_cache     boolean,
            error          text
        )
    """)
    op.execute("CREATE INDEX ingest_runs_started_idx ON ingest_runs (source, started_at DESC)")

    # Supabase exposes the public schema over its REST API; lock it down until
    # we decide what the front end may read. The backend connects as the
    # postgres role, which bypasses RLS.
    for table in ("objects", "gp_elements", "ingest_runs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE ingest_runs")
    op.execute("DROP VIEW gp_latest")
    op.execute("DROP TABLE gp_elements")
    op.execute("DROP TABLE objects")
