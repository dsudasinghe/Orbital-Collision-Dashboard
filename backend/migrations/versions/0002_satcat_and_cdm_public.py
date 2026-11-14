"""SATCAT columns on objects; Space-Track public conjunction messages.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CelesTrak SATCAT: labels and filters for the globe.
    op.execute("""
        ALTER TABLE objects
            ADD COLUMN object_type      text,          -- PAY, R/B, DEB, UNK
            ADD COLUMN ops_status_code  text,
            ADD COLUMN owner            text,
            ADD COLUMN launch_date      date,
            ADD COLUMN launch_site      text,
            ADD COLUMN decay_date       date,
            ADD COLUMN period_min       double precision,
            ADD COLUMN inclination_deg  double precision,
            ADD COLUMN apogee_km        double precision,
            ADD COLUMN perigee_km       double precision,
            ADD COLUMN rcs_m2           double precision,
            ADD COLUMN orbit_center     text,
            ADD COLUMN orbit_type       text,
            ADD COLUMN satcat_updated_at timestamptz
    """)
    op.execute("CREATE INDEX objects_object_type_idx ON objects (object_type)")

    # Space-Track cdm_public. The user agreement restricts redistribution, so
    # this table stays private (RLS on, no policies): the app shows derived
    # stats and links, not raw messages. Satellite IDs are not foreign keys
    # because an event can involve objects outside our GP group.
    op.execute("""
        CREATE TABLE cdm_public (
            cdm_id               bigint PRIMARY KEY,
            created              timestamptz NOT NULL,
            tca                  timestamptz NOT NULL,
            min_rng_m            double precision,
            pc                   double precision,
            emergency_reportable boolean,
            sat1_id              integer NOT NULL,
            sat1_name            text,
            sat1_object_type     text,
            sat2_id              integer NOT NULL,
            sat2_name            text,
            sat2_object_type     text,
            raw                  jsonb NOT NULL,
            fetched_at           timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX cdm_public_tca_idx ON cdm_public (tca)")
    op.execute("CREATE INDEX cdm_public_sat1_idx ON cdm_public (sat1_id, tca)")
    op.execute("CREATE INDEX cdm_public_sat2_idx ON cdm_public (sat2_id, tca)")
    op.execute("ALTER TABLE cdm_public ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE cdm_public")
    op.execute("DROP INDEX objects_object_type_idx")
    op.execute("""
        ALTER TABLE objects
            DROP COLUMN object_type, DROP COLUMN ops_status_code, DROP COLUMN owner,
            DROP COLUMN launch_date, DROP COLUMN launch_site, DROP COLUMN decay_date,
            DROP COLUMN period_min, DROP COLUMN inclination_deg, DROP COLUMN apogee_km,
            DROP COLUMN perigee_km, DROP COLUMN rcs_m2, DROP COLUMN orbit_center,
            DROP COLUMN orbit_type, DROP COLUMN satcat_updated_at
    """)
