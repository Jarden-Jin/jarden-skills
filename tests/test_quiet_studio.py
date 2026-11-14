"""Targeted contract checks for the quiet-studio gallery surface."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skills" / "jarden-ip-visual" / "assets"
GALLERY_JS = ASSETS / "gallery.js"
GALLERY_CSS = ASSETS / "gallery.css"
GALLERY_HTML = ASSETS / "gallery.html"
DESIGN = ROOT / "skills" / "jarden-ip-visual" / "references" / "ui-design-system.md"


class QuietStudioTests(unittest.TestCase):
    def test_gallery_javascript_parses_without_dependencies(self) -> None:
        result = subprocess.run(
            ["node", "--check", str(GALLERY_JS)], cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_template_keeps_runtime_data_placeholders(self) -> None:
        template = GALLERY_HTML.read_text(encoding="utf-8")
        for fragment in ("__GALLERY_CSS__", "__GALLERY_DATA__", "__GALLERY_JS__", 'id="gallery-data"'):
            self.assertIn(fragment, template)

    def test_real_data_and_safe_asset_actions(self) -> None:
        script = GALLERY_JS.read_text(encoding="utf-8")
        for fragment in (
            "data.asset_workflow", "assetWorkflow?.selected", "records(rawCurrent.items)",
            "choose-assets", "approve-asset", "asset-intent", "submit_actions", "asset_ids",
            "expected_revision", "confirmReceipt", "localStorage", "data.stale", "data.readonly",
            "exportIntent", "showDialog", "dependency-note", "assetPlanOverview", "data-zoom-item", "复制「继续」",
        ):
            self.assertIn(fragment, script)
        self.assertNotIn("贝拉", script)
        self.assertNotIn("material-a.png", script)

    def test_responsive_contain_layout_and_boundaries(self) -> None:
        styles = GALLERY_CSS.read_text(encoding="utf-8")
        for fragment in (
            ".project-nav", ".topbar", ".sidebar", ".candidates", "object-fit: contain",
            ".catalog-option", ".capability-boundary", ".technical-evidence", ".mobile-continue", ".dependency-note",
            "@media (max-width: 700px)", "font-size: 16px",
        ):
            self.assertIn(fragment, styles)

    def test_design_spec_records_integration_contract(self) -> None:
        spec = DESIGN.read_text(encoding="utf-8")
        for fragment in ("01「纯白工作室」", "01–21", "choose-assets", "approve-asset", "asset-intent", "stale", "readonly"):
            self.assertIn(fragment, spec)

    def test_runtime_binding_and_safe_actions(self) -> None:
        result = subprocess.run(
            ["node", str(ROOT / "tests" / "test_quiet_studio.cjs")],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
