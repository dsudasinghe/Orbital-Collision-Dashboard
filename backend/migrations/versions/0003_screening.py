"""Watchlist, screening runs and our own conjunction results.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Objects screened against the whole catalog. Objects named in recent
    # Space-Track public CDMs are added automatically at screening time, which
    # also keeps the validation against CDMs running.
    op.execute("""
        CREATE TABLE watchlist (
            norad_cat_id integer PRIMARY KEY REFERENCES objects (norad_cat_id),
            label        text,
            added_at     timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        INSERT INTO watchlist (norad_cat_id, label)
        SELECT norad_cat_id, label FROM (VALUES
            (25544, 'ISS'),
            (48274, 'Tiangong (Tianhe core)'),
            (20580, 'Hubble Space Telescope')
        ) AS v (norad_cat_id, label)
        WHERE EXISTS (SELECT 1 FROM objects o WHERE o.norad_cat_id = v.norad_cat_id)
    """)

    op.execute("""
        CREATE TABLE screening_runs (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            started_at      timestamptz NOT NULL DEFAULT now(),
            finished_at     timestamptz,
            status          text NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'ok', 'failed')),
            window_start    timestamptz NOT NULL,
            window_end      timestamptz NOT NULL,
            step_s          real NOT NULL,
            threshold_km    real NOT NULL,
            watch_ids       integer[],
            watch_count     integer,
            candidate_count integer,
            encounter_count integer,
            timings         jsonb,
            validation      jsonb,
            error           text
        )
    """)

    # One row per close approach found by a run. sat1 is the watchlist object.
    op.execute("""
        CREATE TABLE conjunctions (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id          bigint NOT NULL REFERENCES screening_runs (id) ON DELETE CASCADE,
            sat1_id         integer NOT NULL REFERENCES objects (norad_cat_id),
            sat2_id         integer NOT NULL REFERENCES objects (norad_cat_id),
            tca             timestamptz NOT NULL,
            miss_km         double precision NOT NULL,
            rel_speed_kms   double precision NOT NULL,
            radial_km       double precision NOT NULL,
            in_track_km     double precision NOT NULL,
            cross_track_km  double precision NOT NULL,
            pc_estimate     double precision,
            hard_body_m     double precision,
            sat1_epoch      timestamptz NOT NULL,
            sat2_epoch      timestamptz NOT NULL
        )
    """)
    op.execute("CREATE INDEX conjunctions_run_tca_idx ON conjunctions (run_id, tca)")
    op.execute("CREATE INDEX conjunctions_sat1_idx ON conjunctions (sat1_id, tca)")
    op.execute("CREATE INDEX conjunctions_sat2_idx ON conjunctions (sat2_id, tca)")

    for table in ("watchlist", "screening_runs", "conjunctions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE conjunctions")
    op.execute("DROP TABLE screening_runs")
    op.execute("DROP TABLE watchlist")
