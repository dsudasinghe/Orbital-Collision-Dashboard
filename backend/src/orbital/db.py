from functools import lru_cache

from sqlalchemy import Engine, create_engine

from orbital.config import get_settings


@lru_cache
def get_engine() -> Engine:
    # prepare_threshold=None: Supabase's transaction pooler can't hold prepared
    # statements; disabling them keeps us working on either pooler mode.
    return create_engine(
        get_settings().sqlalchemy_url,
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
    )
