"""Database engine, session factory, and ORM base class.

Three objects, each with a distinct job:

  engine        One per process. Owns the connection pool — the set of real,
                reusable TCP connections to PostgreSQL. Creating it does NOT
                connect; the first query does.

  SessionLocal  A factory that produces Session objects. A Session is a
                short-lived unit of work: it borrows a connection from the pool,
                tracks the objects you have loaded or changed, and writes them
                all out on commit. One session per HTTP request.

  Base          The declarative base every ORM model inherits from. Its
                `.metadata` is the in-memory description of all our tables, and
                it is what Alembic compares against the real database in order
                to generate migrations.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    # Before handing out a pooled connection, send a cheap "are you still there?"
    # ping. Without this, a connection that died while your laptop was asleep (or
    # while the Postgres container restarted) surfaces as a random failure on
    # some unrelated request.
    pool_pre_ping=True,
    # Without an explicit timeout, a *new* TCP connection attempt falls back to
    # whatever the OS/driver defaults to when the target isn't cleanly refusing
    # (a firewall or security software silently dropping the packet instead of
    # sending RST, for example) — which can mean minutes, not seconds, before
    # the attempt gives up. connect_timeout is a libpq/psycopg parameter (in
    # seconds) that bounds that wait, so an unreachable database fails fast and
    # predictably instead of hanging the request — or the test suite — for an
    # unbounded amount of time.
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(
    bind=engine,
    # Do not flush pending changes automatically before every query. We want
    # writes to happen where the service layer says they happen.
    autoflush=False,
    autocommit=False,
    # By default SQLAlchemy expires every object after commit, so touching an
    # attribute afterwards fires another SELECT — which fails once the session is
    # closed. Since FastAPI serialises the response after the request handler
    # returns, keeping objects usable after commit avoids a whole class of
    # confusing DetachedInstanceError bugs.
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all ORM models (first real models arrive in Phase 1)."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed.

    `yield` hands the session to the endpoint; the `finally` block runs after the
    response has been produced, returning the connection to the pool. Note there
    is no commit here — committing is the service layer's decision, not the
    transport layer's.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
