"""Single import point for every module's SQLAlchemy models.

alembic/env.py imports this file before reading Base.metadata, so autogenerate sees all tables.
Each module that introduces models MUST add its import here (see docs/BACKEND_CONVENTIONS.md).
"""

from src.core.database import Base

__all__ = ["Base"]
