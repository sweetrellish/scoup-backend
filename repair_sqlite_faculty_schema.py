#!/usr/bin/env python3
"""Repair SQLite schema drift for academic_faculty using Django model metadata.

Usage:
  python repair_sqlite_faculty_schema.py           # check only
  python repair_sqlite_faculty_schema.py --apply   # apply missing columns
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoupdb.settings")

try:
    import django
    from django.apps import apps
    from django.db import connection
    from django.db import models
except ModuleNotFoundError as exc:
    if exc.name == "django":
        print("Django is not available in the current Python interpreter.")
        print(f"Interpreter in use: {sys.executable}")
        print("Run this script with the project virtualenv interpreter:")
        print("  /home/rellis/scoup-backend/.venv/bin/python repair_sqlite_faculty_schema.py")
        print("Or activate the venv first:")
        print("  source /home/rellis/scoup-backend/.venv/bin/activate")
        print("  python repair_sqlite_faculty_schema.py")
        sys.exit(2)
    raise


def _sqlite_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        value = json.dumps(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _sqlite_type_for_field(field: models.Field) -> str:
    if isinstance(field, (models.IntegerField, models.AutoField, models.BigAutoField)):
        return "INTEGER"
    if isinstance(field, models.FloatField):
        return "REAL"
    if isinstance(field, models.BooleanField):
        return "INTEGER"
    if isinstance(field, (models.ForeignKey, models.OneToOneField)):
        return "INTEGER"
    if isinstance(field, models.JSONField):
        return "TEXT"
    return "TEXT"


def _default_sql_for_field(field: models.Field) -> str | None:
    if not field.has_default():
        return None

    default_value = field.get_default()
    if isinstance(field, models.JSONField):
        if default_value is None:
            default_value = [] if field.default == list else {}
        return _sqlite_literal(default_value)

    return _sqlite_literal(default_value)


def _column_definition(field: models.Field) -> tuple[str, bool]:
    col_type = _sqlite_type_for_field(field)
    default_sql = _default_sql_for_field(field)

    parts = [f'"{field.column}"', col_type]
    warning_needed = False

    # For SQLite ALTER TABLE ADD COLUMN, NOT NULL without default will fail.
    # If a required field lacks a default, we add nullable and warn.
    if (not field.null) and (default_sql is not None):
        parts.append("NOT NULL")
    elif (not field.null) and (default_sql is None):
        warning_needed = True

    if default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")

    return " ".join(parts), warning_needed


def _get_existing_columns(table_name: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        rows = cursor.fetchall()
    return {row[1] for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair missing sqlite columns for academic_faculty")
    parser.add_argument("--apply", action="store_true", help="Apply ALTER TABLE statements")
    args = parser.parse_args()

    try:
        django.setup()
    except Exception as exc:
        print(f"Failed to initialize Django: {exc}")
        print("Check DJANGO_SETTINGS_MODULE and virtual environment setup.")
        return 2

    if connection.vendor != "sqlite":
        print(f"This script only supports sqlite. Current backend: {connection.vendor}")
        print("If your service points to PostgreSQL, use Django migrations for schema changes.")
        return 2

    Faculty = apps.get_model("academic", "Faculty")
    table_name = Faculty._meta.db_table

    existing_columns = _get_existing_columns(table_name)

    missing = []
    warnings = []
    for field in Faculty._meta.local_fields:
        column_name = field.column
        if column_name in existing_columns:
            continue

        col_def, warning_needed = _column_definition(field)
        missing.append((column_name, col_def))
        if warning_needed:
            warnings.append(column_name)

    print(f"table: {table_name}")
    print(f"existing columns: {len(existing_columns)}")

    if not missing:
        print("No missing columns detected.")
        return 0

    print("Missing columns:")
    for column_name, col_def in missing:
        print(f"- {column_name}: {col_def}")

    if warnings:
        print("\nWarnings:")
        for column_name in warnings:
            print(
                f"- {column_name}: required field with no model default was added as nullable to avoid sqlite ALTER failure"
            )

    if not args.apply:
        print("\nCheck mode only. Re-run with --apply to execute ALTER TABLE statements.")
        return 0

    try:
        with connection.cursor() as cursor:
            for _, col_def in missing:
                sql = f'ALTER TABLE "{table_name}" ADD COLUMN {col_def}'
                print(f"Applying: {sql}")
                cursor.execute(sql)
        connection.commit()
    except Exception as exc:
        print(f"Schema repair failed: {exc}")
        return 1

    updated_columns = _get_existing_columns(table_name)
    print(f"\nDone. Column count is now {len(updated_columns)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
