"""Advisory duplicate detection.

Given candidate job data, returns existing applications that may be the same
posting. This is ADVISORY ONLY: it never blocks creation, merges anything, adds a
uniqueness constraint, or writes to the database. It reuses the project's existing
normalisation (`company_key`, `role_key`, `canonicalize_url`) so it agrees with
how applications are stored.

Rules, strongest first:
  1. Exact canonical URL match                              -> strong
  2. Same company + role + same job/requisition id in URL   -> strong
  3. Same company + role + same normalised job description  -> strong
  4. Same company + role only                               -> possible
  (Different company yields no match — rules 2-4 are gated on company + role.)
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalize import (
    canonicalize_url,
    extract_job_id,
    normalize_company,
    normalize_description,
    normalize_role,
)
from app.models.application import Application

STRONG = "strong"
POSSIBLE = "possible"


@dataclass
class DuplicateCandidate:
    application: Application
    confidence: str
    reason: str


def find_duplicate_candidates(
    db: Session,
    *,
    company_name: str,
    role_title: str,
    job_url: str | None = None,
    job_description: str | None = None,
) -> list[DuplicateCandidate]:
    company_key = normalize_company(company_name)
    role_key = normalize_role(role_title)
    url_canonical = canonicalize_url(job_url)
    candidate_job_id = extract_job_id(job_url)
    candidate_description = normalize_description(job_description)

    # application_id -> candidate, so an application is reported once at its
    # strongest reason.
    found: dict[int, DuplicateCandidate] = {}
    base = select(Application).where(Application.deleted_at.is_(None))

    # Rule 1: exact canonical URL — the same posting regardless of typed company.
    if url_canonical:
        rows = db.execute(base.where(Application.job_url_canonical == url_canonical)).scalars()
        for application in rows:
            found[application.id] = DuplicateCandidate(application, STRONG, "Same job URL")

    # Rules 2-4: same normalised company and role. A different company never
    # reaches here, so different-company candidates yield no match.
    if company_key and role_key:
        rows = db.execute(
            base.where(Application.company_key == company_key, Application.role_key == role_key)
        ).scalars()
        for application in rows:
            existing = found.get(application.id)
            if existing and existing.confidence == STRONG:
                continue  # already reported at the strongest confidence (URL)

            confidence, reason = POSSIBLE, "Same company and role"
            application_job_id = extract_job_id(application.job_url)
            if candidate_job_id and application_job_id and candidate_job_id == application_job_id:
                confidence, reason = STRONG, "Same company, role and job ID"
            elif (
                candidate_description
                and normalize_description(application.job_description) == candidate_description
            ):
                confidence, reason = STRONG, "Same company, role and job description"

            found[application.id] = DuplicateCandidate(application, confidence, reason)

    # Strong matches first, then possible; stable by id within each group.
    def sort_key(candidate: DuplicateCandidate) -> tuple[int, int]:
        return (0 if candidate.confidence == STRONG else 1, candidate.application.id)

    return sorted(found.values(), key=sort_key)
