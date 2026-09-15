"""Subjects: the user's own filing system (metadata, never graph nodes — §0)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from secondbrain.db.models import Subject, document_subjects


def normalise_name(name: str) -> str:
    return " ".join(name.split())


def list_subjects(session: Session) -> list[tuple[Subject, int]]:
    """Every subject with its document count, alphabetically."""
    count = func.count(document_subjects.c.document_id)
    stmt = (
        select(Subject, count)
        .outerjoin(document_subjects, document_subjects.c.subject_id == Subject.id)
        .group_by(Subject.id)
        .order_by(Subject.name)
    )
    return [(subject, n) for subject, n in session.execute(stmt).all()]


def get_or_create_subjects(session: Session, names: list[str]) -> list[Subject]:
    """Resolve names to Subject rows, creating the ones that don't exist yet.

    Matching is case-insensitive so "machine learning" and "Machine Learning"
    are one subject; the first spelling entered is the one kept.
    """
    wanted: dict[str, str] = {}
    for raw in names:
        clean = normalise_name(raw)
        if clean:
            wanted.setdefault(clean.casefold(), clean)
    if not wanted:
        return []

    existing = session.execute(
        select(Subject).where(func.lower(Subject.name).in_(list(wanted)))
    ).scalars()
    by_key = {s.name.casefold(): s for s in existing}
    for key, name in wanted.items():
        if key not in by_key:
            subject = Subject(name=name)
            session.add(subject)
            by_key[key] = subject
    session.flush()
    return [by_key[key] for key in wanted]


def create_subject(session: Session, name: str, code: str | None = None) -> Subject:
    (subject,) = get_or_create_subjects(session, [name])
    if code and not subject.code:
        subject.code = code.strip()
        session.flush()
    return subject
