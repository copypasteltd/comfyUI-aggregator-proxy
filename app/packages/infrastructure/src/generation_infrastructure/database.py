from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.settings import Settings


def make_sessions(settings: Settings) -> sessionmaker[Session]:
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True, pool_pre_ping=True, pool_size=10, max_overflow=10, isolation_level="READ COMMITTED", connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=5000"})
    return sessionmaker(engine, expire_on_commit=False)


def request_session(sessions: sessionmaker[Session]) -> Iterator[Session]:
    with sessions() as session:
        yield session
