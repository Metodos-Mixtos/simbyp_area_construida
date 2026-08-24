"""Database configuration and session management for report logging."""

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """Return DATABASE_URL or raise if it is missing."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError(
            "DATABASE_URL environment variable not set. "
            "Required to log reports to the database. "
            "Set it in .env or Cloud Run secret."
        )
    return db_url


def create_db_engine():
    """Create SQLAlchemy engine using safe defaults for Cloud SQL/PostgreSQL."""
    db_url = get_database_url()
    engine = create_engine(
        db_url,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )
    logger.info("Database engine created successfully")
    return engine


def get_session() -> Session:
    """Return a new SQLAlchemy session."""
    engine = create_db_engine()
    session_factory = sessionmaker(bind=engine)
    return session_factory()
