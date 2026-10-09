import os
from sqlalchemy import JSON, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./office.db"))
Session = sessionmaker(engine)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "build_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


def save_job(data: dict):
    with Session.begin() as session:
        existing = session.get(Job, data["id"])
        if existing:
            existing.data = data
        else:
            session.add(Job(id=data["id"], data=data))


def read_job(job_id: str) -> dict | None:
    with Session() as session:
        job = session.get(Job, job_id)
        return job.data if job else None
