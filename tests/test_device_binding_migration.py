"""Migration 0014 (device_id + session_id on DevicePushToken).

The suite runs without migrations (``MIGRATION_MODULES``), so the migration
files are exercised here on their own sqlite database:

* the migration graph reproduces the models (no change left for
  ``makemigrations``);
* a row written by the 0013 schema survives 0014 untouched and unbound —
  ``device_id`` and ``session_id`` NULL, still active — which is what "existing
  rows behave as today until refreshed" means.
"""
import uuid

from django.apps import apps as global_apps
from django.conf import settings
from django.db import connections
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import override_settings

BEFORE = ("notifications", "0013_closure_paused_at")
AFTER = ("notifications", "0014_device_session_binding")


def _with_notifications_migrations():
    modules = {k: v for k, v in settings.MIGRATION_MODULES.items() if k != "notifications"}
    return override_settings(MIGRATION_MODULES=modules)


ALIAS = "notifications_migration_test"


def _scratch_connection(tmp_path):
    params = dict(connections["default"].settings_dict)
    params["NAME"] = str(tmp_path / "migrations.sqlite3")
    connections.settings[ALIAS] = params
    return connections[ALIAS]


def _drop_scratch_connection(conn):
    conn.close()
    del connections[ALIAS]
    connections.settings.pop(ALIAS, None)


def test_the_migrations_reproduce_the_models():
    with _with_notifications_migrations():
        loader = MigrationLoader(None, ignore_no_migrations=True)
        changes = MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(global_apps),
        ).changes(graph=loader.graph, trim_to_apps={"notifications"})
    assert changes.get("notifications", []) == []


def test_an_existing_row_survives_unbound(tmp_path, django_db_blocker):
    """Its own database, outside the test transaction: unblocked, not marked."""
    django_db_blocker.unblock()
    conn = _scratch_connection(tmp_path)
    user_id = uuid.uuid4().hex
    try:
        with _with_notifications_migrations():
            MigrationExecutor(conn).migrate([BEFORE])
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO notifications_devicepushtoken "
                    "(user_id, token, platform, is_active, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [user_id, "legacy-token", "android", True,
                     "2026-11-01 10:00:00", "2026-11-01 10:00:00"],
                )
            MigrationExecutor(conn).migrate([AFTER])
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT token, is_active, device_id, session_id "
                    "FROM notifications_devicepushtoken"
                )
                rows = cursor.fetchall()
    finally:
        _drop_scratch_connection(conn)
        django_db_blocker.restore()
    assert rows == [("legacy-token", 1, None, None)]
