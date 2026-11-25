"""Database layer. SQLite locally; set DATABASE_URL to a Postgres URL on a server."""
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (Boolean, Column, DateTime, Integer, String, Text,
                        create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

ROOT = Path(__file__).parent
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(ROOT / 'news.db').as_posix()}")

engine = create_engine(DATABASE_URL, future=True)
Session = sessionmaker(engine, expire_on_commit=False, future=True)
Base = declarative_base()


class Source(Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True)
    url = Column(String(2048), unique=True, nullable=False)
    priority = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True)


class Topic(Base):
    __tablename__ = "topics"
    id = Column(Integer, primary_key=True)
    name = Column(String(256), unique=True, nullable=False)
    enabled = Column(Boolean, default=True)


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    run_date = Column(String(10), index=True)      # YYYY-MM-DD
    seq = Column(Integer)                          # order within the day (priority first)
    source_type = Column(String(16))               # priority | search
    source_url = Column(String(2048))
    topic = Column(String(256), default="")
    title = Column(Text)
    link = Column(String(2048), unique=True)       # dedupe across days
    published = Column(String(64), default="")
    excerpt = Column(Text, default="")
    text = Column(Text, default="")
    image = Column(String(256), default="")        # main picture, downloaded copy in media/
    images = Column(Text, default="")              # JSON list of further downloaded pictures
    video_id = Column(String(16), default="")      # YouTube id for video items
    transcript = Column(Text, default="")          # JSON list of {t, text} caption lines
    shots = Column(Text, default="")               # JSON list of {t, file, caption, kind} screenshots
    proc = Column(String(24), default="")          # video processing state
    procnote = Column(Text, default="")            # plain-language note about what was skipped or failed
    scraped_at = Column(DateTime, default=datetime.now)


class Run(Base):
    __tablename__ = "runs"
    id = Column(Integer, primary_key=True)
    started = Column(DateTime, default=datetime.now)
    finished = Column(DateTime)
    status = Column(String(16), default="running")  # running | done | error
    phase = Column(String(64), default="")
    message = Column(Text, default="")
    total = Column(Integer, default=0)
    done = Column(Integer, default=0)
    added = Column(Integer, default=0)


def init_db():
    Base.metadata.create_all(engine)
    # Small migration: add the media columns to a database created before they existed.
    from sqlalchemy import inspect, text as sql
    have = {c["name"] for c in inspect(engine).get_columns("items")}
    wanted = {"image": "VARCHAR(256)", "images": "TEXT", "video_id": "VARCHAR(16)",
              "transcript": "TEXT", "shots": "TEXT", "proc": "VARCHAR(24)", "procnote": "TEXT"}
    with engine.begin() as conn:
        for name, kind in wanted.items():
            if name not in have:
                conn.execute(sql(f"ALTER TABLE items ADD COLUMN {name} {kind} DEFAULT ''"))
