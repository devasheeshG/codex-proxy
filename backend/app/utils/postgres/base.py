# Path: app/utils/postgres/base.py
# Description: Database client for PostgreSQL.

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy_utils import create_database, database_exists

from app.config import get_settings

# Get the settings
settings = get_settings()

# Create the engine
engine = create_engine(
    settings.get_postgres_uri(),
    pool_size=5,  # 5 permanent conns per worker (kept open always)
    max_overflow=195,  # 195 overflow conns per worker (created/destroyed on demand)
    pool_pre_ping=True,  # drop stale connections before use
    pool_timeout=0,  # raise immediately if no conn available
    connect_args={"options": "-c timezone=utc"},
)

# Create the session factory
SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create the base class
DatabaseBase = declarative_base()


def init_database() -> None:
    """Create the target database if it does not yet exist. Called by the Alembic entrypoint before migrating."""
    uri = settings.get_postgres_uri()
    if not database_exists(uri):
        create_database(uri)


def get_db():
    """Get Database Session."""
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_cm() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    db = SessionFactory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
