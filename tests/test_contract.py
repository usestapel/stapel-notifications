"""Per-module contract triad + drift gate (contract-pipeline.md §2-3).

stapel-notifications emits its **own** contract triad — ``docs/schema.json``
(drf-spectacular OpenAPI), ``docs/flows.json`` (generate_flow_docs machine
artifact) and ``docs/errors.json`` (generate_error_keys registry) — from a
single-module ``{notifications + core}`` Django instance mounted at the
canonical ``/notifications/api/`` prefix. The frontend codegen consumes these
committed artifacts instead of the monolith aggregate.

Copied from stapel-auth's etalon (``tests/test_contract.py``); adapted to
this module's name and mount prefix. This module has no ``@flow_step``
annotations, so ``flows.json`` is the valid empty array ``[]``.

The emitted schema is **byte-identical to the monolith aggregate's
notifications slice** (paths under ``/notifications/api/`` + their transitive
component closure); see ``test_matches_monolith_notifications_slice`` — the
guarantee the whole repoint rests on.

Regenerate after any change to a serializer / view / url / flow / error key:

    make contract        # or: python -m stapel_notifications._codegen --out docs

then commit ``docs/{schema,flows,errors}.json``. Without regenerating, the
drift gate below fails — the same byte-stable regenerate-and-diff discipline
as ``test_error_keys``.

The harness runs in a **subprocess**: this test process already configured
Django (via conftest, on the bare test urlconf), and the harness needs its
own canonical-prefix urlconf + drf-spectacular singleton — a clean
interpreter is the honest way to exercise exactly what ``make contract``
runs.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PY = sys.version_info[:2]
if _PY != (3, 12):
    _GOT = f"{_PY[0]}.{_PY[1]}"
    _PY312_MSG = (
        "stapel-notifications contract tests require Python 3.12 (the "
        f"CI/monolith pin) — running {_GOT}. drf-spectacular renders "
        "component descriptions (Optional[X] vs X | None) differently "
        "across Python minor versions, so drift/identity checks "
        "emitted+compared under any other minor produce false diffs."
    )
    pytest.skip(
        _PY312_MSG + " Skipping on any non-3.12 interpreter (CI or local) — "
        "the contract canon is only defined on Python 3.12.",
        allow_module_level=True,
    )

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
TRIAD = ("schema.json", "flows.json", "errors.json")
# The fourth artifact (capability-config.md §2): config axes over
# STAPEL_NOTIFICATIONS, emitted from conf.py DEFAULTS + the urls.py gate
# registry + schema.json + the curated docs/capabilities.meta.json.
# Same emit/drift discipline.
ARTIFACTS = TRIAD + ("capabilities.json",)


def _emit(out_dir: Path) -> None:
    for module in ("stapel_notifications._codegen", "stapel_notifications._capabilities"):
        subprocess.run(
            [sys.executable, "-m", module, "--out", str(out_dir)],
            cwd=str(REPO),
            check=True,
            capture_output=True,
        )


def test_contract_artifacts_committed():
    for name in ARTIFACTS:
        assert (DOCS / name).is_file(), f"missing docs/{name} — run `make contract`"
    assert (DOCS / "capabilities.meta.json").is_file(), (
        "missing docs/capabilities.meta.json — the curated layer is "
        "hand-written and committed, not generated"
    )


def test_contract_has_no_drift(tmp_path):
    """Regenerate into a temp dir; committed artifacts must match byte-for-byte."""
    _emit(tmp_path)
    for name in ARTIFACTS:
        committed = (DOCS / name).read_bytes()
        regenerated = (tmp_path / name).read_bytes()
        assert committed == regenerated, (
            f"docs/{name} drifted — run `make contract` and commit docs/{name}"
        )


def test_emission_is_deterministic(tmp_path):
    """Two independent emissions are byte-identical (drift gate is meaningful)."""
    a, b = tmp_path / "a", tmp_path / "b"
    _emit(a)
    _emit(b)
    for name in ARTIFACTS:
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_paths_carry_canonical_prefix():
    """The mount-prefix fix: schema paths + flow endpoints are
    /notifications/api/*, not bare."""
    schema = json.loads((DOCS / "schema.json").read_text())
    assert schema["paths"], "schema has no paths"
    assert all(p.startswith("/notifications/api/") for p in schema["paths"]), (
        "schema paths are not mounted at the canonical /notifications/api/ prefix"
    )
    flows = json.loads((DOCS / "flows.json").read_text())
    for flow in flows:
        for step in flow.get("steps", []):
            for ep in step.get("endpoints", []):
                assert ep["path"].startswith("/notifications/api/"), (
                    f"flow endpoint {ep['path']} is not canonically prefixed"
                )


def test_flows_empty_no_flow_steps():
    """This module has no @flow_step annotations — flows.json is []."""
    flows = json.loads((DOCS / "flows.json").read_text())
    assert flows == []


# --- Byte-identity regression vs the monolith aggregate's notifications slice --
# Only runs in the workspace (the monolith is a sibling repo, absent in module CI).

_MONO = (
    REPO.parent
    / "stapel-example-monolith"
    / "codegen"
    / "generated"
    / "schema.json"
)


def _closure(schema: dict, seeds: set[str]) -> set[str]:
    import re

    comps = schema["components"]["schemas"]
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        name = stack.pop()
        if name in seen or name not in comps:
            continue
        seen.add(name)
        for ref in re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(comps[name])):
            stack.append(ref)
    return seen


def _refs(obj) -> set[str]:
    import re

    return set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(obj)))


@pytest.mark.skipif(
    not _MONO.exists() or os.environ.get("STAPEL_SKIP_MONOLITH_IDENTITY"),
    reason="monolith aggregate not present (module CI checks out only this repo)",
)
def test_matches_monolith_notifications_slice():
    """docs/schema.json == the monolith aggregate's /notifications/api/ slice,
    byte-for-byte.

    Compares path objects and the transitive component closure — the
    envelope (info/servers) is intentionally not compared (it names
    notifications, not the monolith).
    """
    mine = json.loads((DOCS / "schema.json").read_text())
    mono = json.loads(_MONO.read_text())

    mono_paths = {
        p: v for p, v in mono["paths"].items() if p.startswith("/notifications/api/")
    }
    assert set(mine["paths"]) == set(mono_paths), "path set differs from monolith slice"
    for p in mono_paths:
        assert json.dumps(mine["paths"][p], sort_keys=True) == json.dumps(
            mono_paths[p], sort_keys=True
        ), f"path object {p} differs from monolith slice"

    seeds: set[str] = set()
    for v in mono_paths.values():
        seeds |= _refs(v)
    mono_cl = _closure(mono, seeds)
    my_seeds: set[str] = set()
    for v in mine["paths"].values():
        my_seeds |= _refs(v)
    my_cl = _closure(mine, my_seeds)
    assert mono_cl == my_cl, "component closure differs from monolith slice"
    for c in mono_cl:
        assert json.dumps(mine["components"]["schemas"][c], sort_keys=True) == json.dumps(
            mono["components"]["schemas"][c], sort_keys=True
        ), f"component {c} differs from monolith slice"


# --- capabilities.json content sanity (capability-config.md §2) ---------------

_EXPECTED_AXES = {"EMAIL_PROVIDER", "SMS_PROVIDER", "PUSH_PROVIDER"}


def _capabilities() -> dict:
    return json.loads((DOCS / "capabilities.json").read_text())


def test_capabilities_axes_inventory():
    """Three channel-provider selectors, all enum, all in notifications.providers."""
    doc = _capabilities()
    assert {a["key"] for a in doc["axes"]} == _EXPECTED_AXES
    for axis in doc["axes"]:
        assert axis["kind"] == "enum", axis["key"]
        assert axis["group"] == "notifications.providers", axis["key"]


def test_capabilities_every_axis_curated():
    """Every axis carries non-empty curated business semantics."""
    for axis in _capabilities()["axes"]:
        assert axis["curated"]["summary"], axis["key"]
        assert axis["curated"]["business_label"], axis["key"]


def test_capabilities_provider_axes_gate_no_operations():
    """Provider axes select backends; they never unmount endpoints."""
    for axis in _capabilities()["axes"]:
        assert axis["gates"]["operations"] == [], axis["key"]
        assert axis["gates"]["co_gates"] == [], axis["key"]


def test_capabilities_operations_total_matches_schema():
    schema = json.loads((DOCS / "schema.json").read_text())
    methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    total = sum(
        1 for item in schema["paths"].values() for m in item if m in methods
    )
    assert _capabilities()["operations_total"] == total


def test_capabilities_envelope():
    doc = _capabilities()
    import tomllib

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert doc["module"] == pyproject["project"]["name"]
    assert doc["version"] == pyproject["project"]["version"]
    assert doc["provides"]
    assert doc["extension_points"]
    assert doc["requires"]


def test_capabilities_meta_out_of_sync_fails_loudly():
    """A curated-layer gap must be an emission ERROR, never a silent skip."""
    from stapel_tools.capabilities import axis_group_rules, build_capabilities

    from stapel_notifications.conf import DEFAULTS
    from stapel_notifications.urls import GATE_REGISTRY

    schema = json.loads((DOCS / "schema.json").read_text())
    meta = json.loads((DOCS / "capabilities.meta.json").read_text())

    def _build(broken_meta):
        return build_capabilities(
            module="stapel-notifications",
            version="0.0.0",
            defaults=DEFAULTS,
            registry=GATE_REGISTRY,
            schema=schema,
            meta=broken_meta,
            is_axis=lambda k: k in _EXPECTED_AXES,
            axis_group=axis_group_rules(
                suffix={"_PROVIDER": "notifications.providers"}
            ),
            canonical_prefix="/notifications/api",
        )

    # Baseline: intact meta builds.
    assert _build(json.loads(json.dumps(meta)))["axes"]

    # Missing axis entry → loud failure.
    broken = json.loads(json.dumps(meta))
    del broken["axes"]["SMS_PROVIDER"]
    with pytest.raises(SystemExit, match="SMS_PROVIDER"):
        _build(broken)

    # Stale (unknown) axis entry → loud failure.
    broken = json.loads(json.dumps(meta))
    broken["axes"]["FAX_PROVIDER"] = {"summary": "x", "business_label": "x"}
    with pytest.raises(SystemExit, match="FAX_PROVIDER"):
        _build(broken)

    # Empty business_label → loud failure.
    broken = json.loads(json.dumps(meta))
    broken["axes"]["EMAIL_PROVIDER"]["business_label"] = ""
    with pytest.raises(SystemExit, match="business_label"):
        _build(broken)
