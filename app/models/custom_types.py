"""
Cross-dialect custom SQLAlchemy types for PostgreSQL and SQLite compatibility.
"""
import json
from sqlalchemy.types import TypeDecorator, TEXT


class TextArray(TypeDecorator):
    """
    SQLAlchemy type decorator that uses native PostgreSQL ARRAY(TEXT)
    and serializes to JSON string in SQLite.
    """
    impl = TEXT
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import ARRAY, TEXT as pgTEXT
            return dialect.type_descriptor(ARRAY(pgTEXT))
        return dialect.type_descriptor(TEXT)

    def process_bind_param(self, value, dialect):
        if dialect.name == "postgresql":
            return value
        if value is None:
            return None
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if dialect.name == "postgresql":
            return value
        if value is None:
            return None
        try:
            return json.loads(value)
        except Exception:
            return []


class IntegerArray(TypeDecorator):
    """
    SQLAlchemy type decorator that uses native PostgreSQL ARRAY(INTEGER)
    and serializes to JSON string in SQLite.
    """
    impl = TEXT
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import ARRAY, INTEGER as pgINT
            return dialect.type_descriptor(ARRAY(pgINT))
        return dialect.type_descriptor(TEXT)

    def process_bind_param(self, value, dialect):
        if dialect.name == "postgresql":
            return value
        if value is None:
            return None
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if dialect.name == "postgresql":
            return value
        if value is None:
            return None
        try:
            return json.loads(value)
        except Exception:
            return []
