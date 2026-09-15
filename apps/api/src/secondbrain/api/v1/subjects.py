import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from secondbrain.db.engine import get_session
from secondbrain.schemas.documents import SubjectCreate, SubjectOut
from secondbrain.services import subjects as svc

router = APIRouter(prefix="/subjects", tags=["subjects"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[SubjectOut], summary="All subjects with document counts")
def list_subjects(session: SessionDep) -> list[SubjectOut]:
    return [
        SubjectOut(id=s.id, name=s.name, code=s.code, document_count=n)
        for s, n in svc.list_subjects(session)
    ]


@router.post(
    "", response_model=SubjectOut, status_code=status.HTTP_201_CREATED, summary="Create a subject"
)
def create_subject(session: SessionDep, body: SubjectCreate) -> SubjectOut:
    subject = svc.create_subject(session, body.name, body.code)
    session.commit()
    return SubjectOut(id=subject.id, name=subject.name, code=subject.code, document_count=0)


@router.get("/{subject_id}", response_model=SubjectOut)
def get_subject(session: SessionDep, subject_id: uuid.UUID) -> SubjectOut:
    for subject, n in svc.list_subjects(session):
        if subject.id == subject_id:
            return SubjectOut(id=subject.id, name=subject.name, code=subject.code, document_count=n)
    raise HTTPException(404, "subject not found")
