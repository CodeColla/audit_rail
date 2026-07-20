"""
SQLAlchemy Core over the canonical schema in db/schema.sql (PostgreSQL).

There are no ORM models on purpose: the DDL is the single source of truth, and
the reflected `metadata` gives typed Table objects for Core queries.

Postgres runs via docker-compose.yml on host port 5433 (Probo holds 5432).
Alembic joins when the v2 schema lands (see docs/phase3).
"""

from sqlalchemy import MetaData, create_engine

from api.config import settings

DATABASE_URL = settings.database_url

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,   # transparently recycle connections dropped by the server
    pool_size=5,
    max_overflow=10,
)

metadata = MetaData()


def reflect_schema() -> None:
    """Load table definitions from the live DB (called once at startup)."""
    metadata.clear()
    metadata.reflect(bind=engine)


def t(name: str):
    """Reflected Table accessor: t('controls'), t('questions'), ..."""
    return metadata.tables[name]


def get_conn():
    with engine.connect() as conn:
        yield conn
