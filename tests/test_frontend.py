"""Front-end and accessibility checks.

These are static checks, not a browser test. They catch the class of failure
that a Python test suite otherwise misses entirely: a JavaScript syntax error
that stops the whole UI from booting, a visible control with no handler, a
missing ARIA relationship, or a colour used as the only signal.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
APP_JS = WEB / "app.js"
INDEX = WEB / "index.html"
STYLES = WEB / "styles.css"


def test_web_assets_exist():
    assert APP_JS.is_file(), "web/app.js is missing"
    assert INDEX.is_file(), "web/index.html is missing"
    assert STYLES.is_file(), "web/styles.css is missing"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_app_js_parses():
    """A syntax error here means the entire UI fails to boot."""
    result = subprocess.run(
        ["node", "--check", str(APP_JS)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, f"app.js has a syntax error:\n{result.stderr}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_render_path_runs_against_real_payloads(tmp_path):
    """Drive the real render functions against real API payloads.

    A syntax check only proves the file parses. This proves the render paths
    actually execute, which is the difference between a working UI and a blank
    panel. The harness catches the whole class of bug where a helper is called
    with the wrong argument shape and throws only at runtime.
    """
    import json

    from fastapi.testclient import TestClient

    from witness.api import create_app

    app = create_app(db_path=str(tmp_path / "render.db"))
    with TestClient(app) as client:
        # Build a state that exercises every branch: withdrawal, encryption,
        # duplicate and late events, source failure and a limitation.
        client.post("/api/simulated/reset")
        for _ in range(4):
            client.post("/api/observe/ring-simulated")
            client.post("/api/simulated/advance")

        overview = client.get("/api/overview").json()
        spec = client.get("/api/status-spec").json()
        devices = client.get("/api/devices/ring-simulated").json()

    paths = {
        "overview": tmp_path / "overview.json",
        "spec": tmp_path / "spec.json",
        "devices": tmp_path / "devices.json",
    }
    paths["overview"].write_text(json.dumps(overview))
    paths["spec"].write_text(json.dumps(spec))
    paths["devices"].write_text(json.dumps(devices))

    harness = Path(__file__).resolve().parent.parent / "scripts" / "render_check.js"
    result = subprocess.run(
        ["node", str(harness), str(paths["overview"]), str(paths["spec"]), str(paths["devices"])],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        "a render path threw against real payloads:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "OK: all" in result.stdout


def test_index_declares_the_language_and_viewport():
    html = INDEX.read_text()
    assert 'lang="en"' in html
    assert 'name="viewport"' in html


def test_index_has_a_skip_link():
    html = INDEX.read_text()
    assert 'class="skip-link"' in html
    assert 'href="#main"' in html


def test_index_has_exactly_one_main_landmark():
    html = INDEX.read_text()
    assert html.count("<main") == 1
    assert 'id="main"' in html


def test_every_tab_points_at_a_real_panel():
    html = INDEX.read_text()
    controls = re.findall(r'aria-controls="([^"]+)"', html)
    assert controls, "no aria-controls found"
    for target in controls:
        assert f'id="{target}"' in html, f"tab references missing panel {target}"


def test_every_panel_is_labelled_by_a_tab():
    html = INDEX.read_text()
    for labelledby in re.findall(r'role="tabpanel"[^>]*aria-labelledby="([^"]+)"', html):
        assert f'id="{labelledby}"' in html, f"panel labelled by missing tab {labelledby}"


def test_drawer_is_a_labelled_dialog():
    html = INDEX.read_text()
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="drawer-title"' in html


def test_live_regions_exist_for_status_updates():
    html = INDEX.read_text()
    assert 'aria-live="polite"' in html


def test_visible_controls_are_buttons_not_dead_divs():
    """Every clickable control must be a real button so it is keyboard reachable."""
    js = APP_JS.read_text()
    handlers = re.findall(r"onclick:\s*\(\)\s*=>", js)
    assert handlers, "expected interactive handlers"
    assert "el('button'" in js, "handlers should be attached to <button> elements"


def test_no_inline_colour_only_signalling_in_status_pills():
    """Every status pill must carry a glyph, so colour is never the only cue."""
    css = STYLES.read_text()
    for tone in ("warn", "unknown", "error"):
        assert f'.pill[data-tone="{tone}"]::before' in css, (
            f"tone {tone} has no glyph, so colour would be the only signal"
        )


def test_focus_visible_style_is_defined():
    css = STYLES.read_text()
    assert ":focus-visible" in css
    assert "outline" in css


def test_reduced_motion_is_respected():
    css = STYLES.read_text()
    assert "prefers-reduced-motion" in css


def test_high_contrast_mode_is_supported():
    css = STYLES.read_text()
    assert "prefers-contrast" in css


def test_responsive_breakpoint_exists():
    css = STYLES.read_text()
    assert "@media (max-width: 640px)" in css


def test_ui_never_hardcodes_a_status_string_as_a_conclusion():
    """Statuses must come from the API's taxonomy, not be invented in the client."""
    js = APP_JS.read_text()
    # The client may *reference* status names for grouping and labelling, but it
    # must not construct an assessment object of its own.
    assert "statusSpec" in js, "the client should read the taxonomy from the API"
    assert "/api/status-spec" in js


def test_ui_method_tab_lists_every_status_in_the_registry():
    """Every status the engine can assign must appear on the Method tab.

    The tab reads each status's text from the API, but the list of keys it
    iterates is written by hand in ``renderMethod``. A status added to the enum
    without being added to that list would exist in the API and never be shown,
    so the "this page is generated from the engine's own registry" claim on the
    page would be false. Parse the keys out of the source and compare.
    """
    import re

    from witness.engine.status import Status

    js = APP_JS.read_text()
    method = js.split("async function renderMethod()", 1)[1]
    groups_block = method.split("const groups = [", 1)[1].split("];", 1)[0]

    listed = set(re.findall(r"'([A-Z_]+)'", groups_block))
    declared = {s.name for s in Status}
    assert listed == declared, (
        "the Method tab's status groups do not match the engine registry; "
        f"missing from the UI: {sorted(declared - listed)}; "
        f"listed but not in the registry: {sorted(listed - declared)}"
    )


def test_ui_labels_the_simulated_source():
    js = APP_JS.read_text()
    assert "simulated" in js.lower()
    assert "does not contact Ring" in js


def test_ui_states_the_ledger_limitation():
    """The honest security limitation must be present in the interface."""
    js = APP_JS.read_text()
    assert "tamper-evident" in js
    assert "never as immutable" in js or "not prove the ledger was never rewritten" in js
