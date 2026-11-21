"""Regenerate database/schema.sql from the SQLAlchemy models.

    python -m scripts.dump_schema > ../database/schema.sql
"""
import os

os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ.setdefault("CREDENTIAL_KEYS", "k:" + "A" * 43 + "=")

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base  # noqa: E402

print("-- CameraScope PostgreSQL schema (generated from backend/app/models.py)")
print("-- The backend also creates these tables automatically on startup.\n")
print("BEGIN;\n")
dialect = postgresql.dialect()
for table in Base.metadata.sorted_tables:
    print(str(CreateTable(table).compile(dialect=dialect)).strip() + ";\n")
    for idx in sorted(table.indexes, key=lambda i: i.name):
        print(str(CreateIndex(idx).compile(dialect=dialect)).strip() + ";")
    print()
print("COMMIT;")
