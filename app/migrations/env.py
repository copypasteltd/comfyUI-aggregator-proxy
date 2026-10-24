from __future__ import annotations

from alembic import context
from generation_infrastructure.models import Base
from generation_infrastructure.settings import Settings
from sqlalchemy import create_engine, pool

config = context.config
target_metadata = Base.metadata


def run() -> None:
    settings = config.attributes.get("settings") or Settings()
    if context.is_offline_mode():
        context.configure(url=settings.database_url.get_secret_value(), target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(settings.database_url.get_secret_value(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
