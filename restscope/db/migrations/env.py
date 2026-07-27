"""Configure Alembic so database migrations use RESTScope's SQLAlchemy metadata.

Alembic imports this module when it upgrades a database.  The two functions
below select offline SQL generation or a real database connection; normal
application code should use :mod:`restscope.db.bootstrap` instead.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from restscope.db import orm  # noqa: F401
from restscope.db.base import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations offline for the repository and database persistence boundary.

    The annotated arguments and return type define the data boundary used by callers.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations online for the repository and database persistence boundary.

    The annotated arguments and return type define the data boundary used by callers.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
