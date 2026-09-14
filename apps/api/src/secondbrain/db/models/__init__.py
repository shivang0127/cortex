"""ORM models. Import every model here so Alembic autogenerate sees the full metadata."""

from secondbrain.db.models.job import Job

__all__ = ["Job"]
