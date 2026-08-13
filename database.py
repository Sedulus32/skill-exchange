import os

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Read the database URL from the environment variable.
# If DATABASE_URL is set, use PostgreSQL.
# If it is not set, fall back to SQLite for local testing.
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # PostgreSQL (production)
    engine = create_engine(DATABASE_URL)
else:
    # SQLite database file (created automatically in the project folder)
    SQLALCHEMY_DATABASE_URL = "sqlite:///./skill_exchange.db"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )

# SessionLocal is used to create database sessions for each request
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class that all models will inherit from
Base = declarative_base()