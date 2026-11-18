"""Detected manoeuvres and per-object anomaly scores.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE detection_runs (
            id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            started_at     timestamptz NOT NULL DEFAULT now(),
            finished_at    timestamptz,
            status         text NOT NULL DEFAULT 'running'
                           CHECK (status IN ('running', 'ok', 'failed')),
            objects_scored integer,
            manoeuvres     integer,
            params         jsonb,
            error          text
        )
    """)

    # One row per detected event; re-detection updates it in place.
    op.execute("""
        CREATE TABLE manoeuvres (
            norad_cat_id  integer NOT NULL REFERENCES objects (norad_cat_id),
            epoch         timestamptz NOT NULL,  -- first element set after the burn
            -- 'anomaly' is an unexplained change on an object that can't manoeuvre
            kind          text NOT NULL CHECK (kind IN ('orbit raise', 'orbit lower', 'plane change', 'anomaly')),
            da_km         double precision NOT NULL,
            d_inc_deg     double precision NOT NULL,
            z_max         double precision NOT NULL,
            pairs_merged  integer NOT NULL,
            run_id        bigint NOT NULL REFERENCES detection_runs (id),
            PRIMARY KEY (norad_cat_id, epoch)
        )
    """)
    op.execute("CREATE INDEX manoeuvres_epoch_idx ON manoeuvres (epoch)")

    # Latest anomaly score per object (largest robust |z| of its newest pair).
    op.execute("""
        CREATE TABLE anomaly_scores (
            norad_cat_id  integer PRIMARY KEY REFERENCES objects (norad_cat_id),
            score         double precision NOT NULL,
            scored_at     timestamptz NOT NULL DEFAULT now(),
            run_id        bigint NOT NULL REFERENCES detection_runs (id)
        )
    """)

    for table in ("detection_runs", "manoeuvres", "anomaly_scores"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE anomaly_scores")
    op.execute("DROP TABLE manoeuvres")
    op.execute("DROP TABLE detection_runs")
