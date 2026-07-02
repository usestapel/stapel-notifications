"""CI gate: every requested notification type must be registered.

Statically scans the host project (and any extra paths) for
``request_notification(...)`` call sites and checks each literal first
argument (or ``notification_type=`` keyword) against the effective type
registry (built-in catalog + STAPEL_NOTIFICATIONS['TYPES']).

A call that passes ``content_html=``/``content_text=`` is exempt — the
raw-content escape hatch renders without a registered type/template.

Limitations (documented, not silent): only literal, same-codebase call
sites can be verified. Dynamic types (variables, f-strings) are reported
as warnings; calls made from other services are invisible to this check.

    python manage.py check_notifications
    python manage.py check_notifications src/ libs/billing/
"""

import ast
import os
from dataclasses import dataclass

from django.core.management.base import BaseCommand

from stapel_notifications.routing import registered_types

SKIP_DIRS = {
    "migrations", "__pycache__", ".git", "node_modules", "venv", ".venv",
    "htmlcov", "build", "dist", ".claude", "worktrees",
}


@dataclass
class Issue:
    level: str  # "error" | "warning"
    path: str
    line: int
    message: str

    def __str__(self):
        return f"{self.level.upper()} {self.path}:{self.line}: {self.message}"


def _iter_python_files(paths):
    for base in paths:
        if os.path.isfile(base):
            if base.endswith(".py"):
                yield base
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(root, name)


def _request_notification_calls(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name == "request_notification":
            yield node


def _type_arg(call):
    """The notification-type argument expression, or None."""
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "notification_type":
            return kw.value
    return None


def _passes_content(call):
    return any(
        kw.arg in ("content_html", "content_text") and
        not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
        for kw in call.keywords
    )


def check_paths(paths) -> list[Issue]:
    issues: list[Issue] = []
    known = set(registered_types())
    if not known:
        issues.append(Issue(
            "warning", "-", 0,
            "notification type registry is empty — nothing to check against "
            "(is stapel_notifications configured?)",
        ))
        return issues

    for path in _iter_python_files(paths):
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for call in _request_notification_calls(tree):
            if _passes_content(call):
                continue  # escape hatch: renders without a registered type
            type_arg = _type_arg(call)
            if type_arg is None:
                issues.append(Issue(
                    "error", path, call.lineno,
                    "request_notification() without a notification type",
                ))
            elif isinstance(type_arg, ast.Constant) and isinstance(type_arg.value, str):
                if type_arg.value not in known:
                    issues.append(Issue(
                        "error", path, call.lineno,
                        f"unregistered notification type '{type_arg.value}' — "
                        "register it via STAPEL_NOTIFICATIONS['TYPES'] or pass "
                        "content_html/content_text",
                    ))
            else:
                issues.append(Issue(
                    "warning", path, call.lineno,
                    "dynamic notification type — cannot verify statically",
                ))
    return issues


class Command(BaseCommand):
    help = (
        "Statically check that every request_notification() call site uses "
        "a registered notification type (or the content_* escape hatch)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "paths", nargs="*", default=None,
            help="Files/directories to scan (default: current directory)",
        )

    def handle(self, *args, **options):
        paths = options["paths"] or ["."]
        issues = check_paths(paths)

        errors = [i for i in issues if i.level == "error"]
        warnings = [i for i in issues if i.level == "warning"]
        for issue in issues:
            writer = self.stderr if issue.level == "error" else self.stdout
            style = self.style.ERROR if issue.level == "error" else self.style.WARNING
            writer.write(style(str(issue)))

        if errors:
            self.stderr.write(self.style.ERROR(
                f"check_notifications: {len(errors)} error(s), "
                f"{len(warnings)} warning(s)"
            ))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(
            f"check_notifications: OK ({len(warnings)} warning(s))"
        ))
