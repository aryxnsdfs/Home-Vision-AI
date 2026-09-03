"""Requested site features must reach the things the user actually looks at.

The backend filled `outdoor_areas` with real plot coordinates and a full asset
list for a long time, and nothing on the front end read it. A brief asking for
"parking for two cars" produced parking that appeared in no view and no drawing,
and the acceptance suite passed it because it inspected the JSON.

These are source-level assertions rather than renders, which is a real
limitation: they cannot tell you the parking is drawn *correctly*. What they can
do is fail loudly if a refactor drops the wiring again, which is exactly how the
bug arrived - `git log -S"outdoor_areas" -- src/` blames a commit that removed a
feature set and took the site rendering with it.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SCENE = ROOT / "src" / "components" / "SceneCanvas.jsx"
REPORT = ROOT / "src" / "pdf" / "ArchitectReport.jsx"


def _read(path):
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    return path.read_text(encoding="utf-8")


def test_the_3d_scene_reads_outdoor_areas():
    source = _read(SCENE)
    assert "outdoor_areas" in source, "the viewer does not read site features at all"
    assert "SiteLayer" in source, "no site layer component is mounted"


def test_the_site_layer_is_actually_rendered_not_just_defined():
    source = _read(SCENE)
    # Defining the component and forgetting to mount it is the same bug again.
    assert "<SiteLayer" in source, "SiteLayer is defined but never rendered"


def test_the_engineering_report_reads_outdoor_areas():
    source = _read(REPORT)
    assert "outdoor_areas" in source, "the drawings do not read site features at all"


def test_the_report_grows_its_bounds_to_include_the_site():
    # Parking usually sits outside the building footprint. Drawing it without
    # widening the viewBox puts it off the edge of the sheet.
    source = _read(REPORT)
    assert "projectSiteAreas" in source
    assert "siteAreas.forEach" in source or "boundsWithSite" in source, (
        "site features are drawn but the drawing bounds ignore them"
    )


def test_site_features_are_not_counted_as_habitable_rooms():
    # They are floor area on the plot, not built-up area, and they must not be
    # mixed into the room schedule as if someone could sleep in them.
    report = _read(REPORT)
    assert "SITE_FILLS" in report, "site features share the room styling"
