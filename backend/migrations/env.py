from logging.config import fileConfig

from alembic import context

from orbital.db import get_engine

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

# Migrations are hand-written SQL; there is no ORM metadata to autogenerate from.
with get_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
