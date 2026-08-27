from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


# BlazeGuard SQLite database
DATABASE_URL = "sqlite:///./blazeguard.db"

# Create database engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)


# Base class for all database models
class Base(DeclarativeBase):
    pass


# Database session factory
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)