"""Seed the demo database with fictional data for GitHub screenshots.

This is a development / presentation tool, not part of the product. It exists so
you can run the real, unchanged application against a throwaway database full of
obviously-fictional job applications, take clean screenshots, and then go back to
your real data by simply closing the terminal.

SAFETY — this script cannot touch your real data:

  * It refuses to run unless the configured database is named exactly
    `jobops_demo`. Your real database is `jobops`; the guard makes wiping it
    impossible, the same way `reset_e2e_db.py` protects the test database.
  * It writes fictional CV bytes into `demo-data/documents` only, and refuses to
    clear any documents folder whose path does not contain "demo".
  * It never reads, copies, or exposes anything under your real `data/documents`.

USAGE (PowerShell), from the backend/ directory:

    $env:JOBOPS_ENV_FILE = ".env.demo"
    .\.venv\Scripts\python.exe scripts\seed_demo.py

Running it repeatedly is safe: it wipes the demo database and demo documents
first, then reseeds, so you always get the same five records with no duplicates.

All companies, people, emails, URLs and CVs below are invented. The `.example`
top-level domain is reserved by the IANA precisely so it can never be a real
site.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Import order matters: `app.config` reads JOBOPS_ENV_FILE at import time, so by
# the time we touch the database the engine is already bound to whatever that
# env file specifies. The guard below then proves it is the demo database.
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.enums import (  # noqa: E402
    ApplicationChannel,
    ApplicationStatus,
    DocumentKind,
    EventType,
)
from app.schemas.application import ApplicationCreate  # noqa: E402
from app.schemas.event import EventCreate  # noqa: E402
from app.services.applications import (  # noqa: E402
    attach_submitted_cv,
    change_status,
    create_application,
)
from app.services.documents import store_document  # noqa: E402
from app.services.events import add_manual_event  # noqa: E402

REQUIRED_DATABASE_NAME = "jobops_demo"
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Children before parents, so foreign keys never block the truncate.
TABLES_IN_DELETION_ORDER = ("application_events", "applications", "documents")

NOW = datetime.now(UTC)


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


def days_ahead(n: int) -> datetime:
    return NOW + timedelta(days=n)


# --- Safety guards --------------------------------------------------------


def guard_database() -> str:
    """Refuse to run against anything but the demo database."""
    name = make_url(settings.database_url).database
    if name != REQUIRED_DATABASE_NAME:
        raise SystemExit(
            "REFUSING TO SEED.\n"
            f"  The app is configured for database {name!r}, but this script only\n"
            f"  ever touches {REQUIRED_DATABASE_NAME!r}.\n\n"
            "  You are probably in real mode. Enable demo mode first:\n"
            '      $env:JOBOPS_ENV_FILE = ".env.demo"\n'
            "  then run this script again."
        )
    return settings.database_url


def guard_documents_path() -> Path:
    """Refuse to clear any documents folder that is not clearly the demo one."""
    path = settings.documents_path
    if "demo" not in str(path).lower():
        raise SystemExit(
            "REFUSING TO SEED.\n"
            f"  DOCUMENTS_ROOT resolves to {path}, which does not look like the\n"
            "  demo folder. Expected a path containing 'demo'."
        )
    return path


# --- Database preparation -------------------------------------------------


def create_database_if_missing(url_string: str) -> None:
    """CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT."""
    url = make_url(url_string)
    admin_engine = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    with admin_engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": REQUIRED_DATABASE_NAME},
        ).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{REQUIRED_DATABASE_NAME}"'))
            print(f"  created database {REQUIRED_DATABASE_NAME}")
    admin_engine.dispose()


def run_migrations(url_string: str) -> None:
    """Build the schema with the real migrations, in a subprocess."""
    environment = {**os.environ, "DATABASE_URL": url_string}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=environment,
        check=True,
        capture_output=True,
    )
    print("  migrations applied")


def wipe(url_string: str, documents_path: Path) -> None:
    """Empty the demo database and demo documents folder (idempotency)."""
    engine = create_engine(url_string, connect_args={"connect_timeout": 5})
    with engine.begin() as connection:
        for table in TABLES_IN_DELETION_ORDER:
            connection.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    engine.dispose()

    if documents_path.exists():
        shutil.rmtree(documents_path)
    documents_path.mkdir(parents=True, exist_ok=True)
    print("  demo database and documents cleared")


# --- Fictional content ----------------------------------------------------


def demo_pdf(title: str, body: str) -> bytes:
    """A tiny, valid-enough PDF whose bytes are clearly fictional demo content."""
    return (
        f"%PDF-1.4\n"
        f"% JobOps DEMO document — fictional, for screenshots only\n"
        f"1 0 obj<</Type/Catalog>>endobj\n"
        f"trailer<</Root 1 0 R>>\n"
        f"%%EOF\n"
        f"{title}\n{body}\n"
    ).encode()


@dataclass
class Step:
    """One status transition on the timeline."""

    status: ApplicationStatus
    day: int  # days ago
    note: str | None = None


@dataclass
class DemoApplication:
    company: str
    role: str
    channel: ApplicationChannel
    location: str
    work_mode: str
    job_url: str
    notes: str
    cv_key: str  # which fictional CV to attach
    saved_day: int  # days ago the record was first saved
    steps: list[Step]
    extra_events: list[EventCreate] = field(default_factory=list)


# Two fictional CVs, reused across applications to show the library working.
CV_LIBRARY = {
    "fullstack_node": {
        "kind": DocumentKind.CV,
        "filename": "Demo_CV_Fullstack_Node.pdf",
        "label": "Fullstack CV — Node-heavy (demo)",
        "notes": "Fictional demo CV. Leads with Node/React and product work.",
        "body": "Fictional demo CV. Fullstack — Node.js, React, TypeScript, PostgreSQL.",
    },
    "backend_python": {
        "kind": DocumentKind.CV,
        "filename": "Demo_CV_Backend_Python.pdf",
        "label": "Backend & Infra CV — Python-heavy (demo)",
        "notes": "Fictional demo CV. Leads with Python, data pipelines and infra.",
        "body": "Fictional demo CV. Backend — Python, FastAPI, data pipelines, AWS.",
    },
}

# A cover letter, present in the library to show more than one document kind on
# the Documents screen. Not attached as a submitted CV.
EXTRA_LIBRARY_DOCS = [
    {
        "kind": DocumentKind.COVER_LETTER,
        "filename": "Demo_Cover_Letter.pdf",
        "label": "Cover letter template (demo)",
        "notes": "Fictional demo cover letter used as a starting template.",
        "body": "Fictional demo cover letter. Dear Hiring Team, ...",
    },
]

DEMO_APPLICATIONS = [
    DemoApplication(
        company="Northstar Labs",
        role="Senior Backend Engineer",
        channel=ApplicationChannel.LINKEDIN,
        location="Tel Aviv",
        work_mode="remote",
        job_url="https://careers.northstarlabs.example/jobs/senior-backend-engineer",
        notes="Backend platform team (Python/Go). Comp range discussed and reasonable.",
        cv_key="backend_python",
        saved_day=24,
        steps=[
            Step(ApplicationStatus.APPLIED, 23, "Applied via LinkedIn."),
            Step(ApplicationStatus.RECRUITER_CONTACT, 20, "Recruiter reached out about the role."),
            Step(ApplicationStatus.HR_INTERVIEW, 16, "Intro call with the talent team."),
            Step(ApplicationStatus.TECHNICAL_INTERVIEW, 9, "Advanced to the technical round."),
        ],
        extra_events=[
            EventCreate(
                event_type=EventType.INTERVIEW_SCHEDULED,
                summary="Technical interview — system design round",
                body="Video call with two senior engineers.",
                scheduled_for=days_ahead(3),
            ),
        ],
    ),
    DemoApplication(
        company="BluePeak Systems",
        role="Fullstack Developer",
        channel=ApplicationChannel.REFERRAL,
        location="Herzliya",
        work_mode="hybrid",
        job_url="https://bluepeaksystems.example/careers/fullstack-developer",
        notes="Referred by a former teammate. Product-focused team.",
        cv_key="fullstack_node",
        saved_day=38,
        steps=[
            Step(ApplicationStatus.APPLIED, 37, "Applied with a referral."),
            Step(ApplicationStatus.HR_INTERVIEW, 32, "Screening call with HR."),
            Step(ApplicationStatus.TECHNICAL_INTERVIEW, 24, "Pair-programming exercise."),
            Step(ApplicationStatus.FINAL_INTERVIEW, 14, "Final round with the team lead."),
            Step(ApplicationStatus.OFFER, 5, "Offer received — reviewing the details."),
        ],
        extra_events=[
            EventCreate(
                event_type=EventType.OFFER_RECEIVED,
                summary="Offer received",
                body="Written offer sent through. Deciding by the end of the month.",
                occurred_at=days_ago(5),
            ),
        ],
    ),
    DemoApplication(
        company="Meridian AI",
        role="Machine Learning Engineer",
        channel=ApplicationChannel.COMPANY_SITE,
        location="Tel Aviv",
        work_mode="onsite",
        job_url="https://meridian-ai.example/join/machine-learning-engineer",
        notes="Applied research team, NLP focus.",
        cv_key="backend_python",
        saved_day=12,
        steps=[
            Step(ApplicationStatus.APPLIED, 11, "Applied on the company careers page."),
            Step(ApplicationStatus.RECRUITER_CONTACT, 8, "Recruiter emailed to set up a call."),
            Step(ApplicationStatus.HR_INTERVIEW, 3, "Intro HR interview completed."),
        ],
        extra_events=[
            EventCreate(
                event_type=EventType.INTERVIEW_SCHEDULED,
                summary="Hiring-manager interview",
                body="Call with the ML team lead.",
                scheduled_for=days_ahead(2),
            ),
        ],
    ),
    DemoApplication(
        company="Cedar Technologies",
        role="DevOps Engineer",
        channel=ApplicationChannel.JOB_BOARD,
        location="Remote (EU)",
        work_mode="remote",
        job_url="https://jobs.cedartech.example/postings/devops-engineer",
        notes="Kubernetes-heavy platform role. Good process, not a fit this round.",
        cv_key="backend_python",
        saved_day=45,
        steps=[
            Step(ApplicationStatus.APPLIED, 44, "Found on a job board and applied."),
            Step(ApplicationStatus.HR_INTERVIEW, 40, "Screening call."),
            Step(ApplicationStatus.TECHNICAL_INTERVIEW, 34, "Take-home + review session."),
            Step(
                ApplicationStatus.REJECTED,
                28,
                "Rejected after the technical round — they went with a more senior candidate.",
            ),
        ],
    ),
    DemoApplication(
        company="Atlas Software",
        role="Python Developer",
        channel=ApplicationChannel.LINKEDIN,
        location="Ramat Gan",
        work_mode="hybrid",
        job_url="https://atlas-software.example/careers/python-developer",
        notes="Django + data pipelines. Waiting to hear back.",
        cv_key="backend_python",
        saved_day=5,
        steps=[
            Step(ApplicationStatus.APPLIED, 4, "Applied via LinkedIn."),
        ],
    ),
]


# --- Seeding --------------------------------------------------------------


def seed() -> None:
    session = SessionLocal()
    try:
        # Store the fictional documents once; keep their ids for attaching.
        cv_ids: dict[str, int] = {}
        for key, spec in CV_LIBRARY.items():
            document, _ = store_document(
                session,
                kind=spec["kind"],
                content=demo_pdf(spec["label"], spec["body"]),
                original_filename=spec["filename"],
                content_type="application/pdf",
                label=spec["label"],
                notes=spec["notes"],
            )
            cv_ids[key] = document.id

        for spec in EXTRA_LIBRARY_DOCS:
            store_document(
                session,
                kind=spec["kind"],
                content=demo_pdf(spec["label"], spec["body"]),
                original_filename=spec["filename"],
                content_type="application/pdf",
                label=spec["label"],
                notes=spec["notes"],
            )

        for demo in DEMO_APPLICATIONS:
            # Create as SAVED first, then walk the status history with backdated
            # timestamps through the real service layer — the same code path the
            # UI uses, so every status change writes a proper timeline event.
            application = create_application(
                session,
                ApplicationCreate(
                    company_name=demo.company,
                    role_title=demo.role,
                    status=ApplicationStatus.SAVED,
                    application_channel=demo.channel,
                    job_url=demo.job_url,
                    job_description=(
                        f"{demo.role} at {demo.company}. Fictional demo posting used "
                        "only for screenshots. Responsibilities include building and "
                        "operating services end to end."
                    ),
                    location=demo.location,
                    work_mode=demo.work_mode,
                    notes=demo.notes,
                ),
            )

            for step in demo.steps:
                change_status(
                    session,
                    application.id,
                    to_status=step.status,
                    note=step.note,
                    occurred_at=days_ago(step.day),
                )

            attach_submitted_cv(
                session,
                application.id,
                cv_ids[demo.cv_key],
                note="CV submitted with the application.",
                occurred_at=days_ago(demo.saved_day - 1),
            )

            for event in demo.extra_events:
                add_manual_event(session, application.id, event)

            # The `created` event and applications.created_at are stamped at
            # "now" by the service layer (its occurred_at is not caller-settable).
            # For coherent screenshots, backdate just those two, on the DEMO
            # database only, to the day the record was first saved.
            saved_at = days_ago(demo.saved_day)
            session.execute(
                text(
                    "UPDATE application_events SET occurred_at = :ts "
                    "WHERE application_id = :id AND event_type = 'created'"
                ),
                {"ts": saved_at, "id": application.id},
            )
            session.execute(
                text("UPDATE applications SET created_at = :ts WHERE id = :id"),
                {"ts": saved_at, "id": application.id},
            )
            session.commit()

        print(f"  seeded {len(DEMO_APPLICATIONS)} fictional applications")
    finally:
        session.close()


def main() -> None:
    print("Seeding JobOps demo data…")
    url_string = guard_database()
    documents_path = guard_documents_path()

    create_database_if_missing(url_string)
    run_migrations(url_string)
    wipe(url_string, documents_path)
    seed()

    print("Done. Start the app with JOBOPS_ENV_FILE=.env.demo to view it.")


if __name__ == "__main__":
    main()
