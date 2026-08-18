"""ORM models.

Every model module must be imported here so that importing `app.models` is
enough to register all tables on `Base.metadata`. `alembic/env.py` relies on
that: a model this package does not import is a model Alembic cannot see — and
autogenerate would happily write a migration that drops it.
"""

from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.document import Document

__all__ = ["Application", "ApplicationEvent", "Document"]
