"""Canonical-prefix URLconf for contract emission (contract-pipeline.md §2).

The pytest urlconf mounts notifications *bare* (``stapel_notifications.urls``
→ ``/devices/``). That is the repoint bug: the monolith aggregate — and
therefore every frontend projection — serves notifications under its
canonical public API prefix, ``/notifications/api/devices/``.

This URLconf reproduces the monolith mount **exactly** (svc-app/core/urls.py
line 39: notifications alone, no sibling co-mount, under
``notifications/api/``), so drf-spectacular emits ``/notifications/api/...``
paths (and the matching ``notifications_api_*`` operationIds) and
``generate_flow_docs`` resolves flow endpoints to the same. Getting this
prefix exact is the make-or-break for a zero-diff repoint
(contract-pipeline.md §2, §9).
"""
from django.conf.urls import include
from django.urls import path

urlpatterns = [
    path("notifications/api/", include("stapel_notifications.urls")),
]
