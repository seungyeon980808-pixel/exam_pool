import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import db, routes_authoring as ra


class TestSourceCropRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        data = ra.create_session(ra.SessionIn(provider="mock"))
        self.sid = data["session"]["id"]

    def tearDown(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM authoring_session WHERE id=?", (self.sid,))

    @staticmethod
    def _body(payload: bytes, source_hash: str | None = None) -> ra.FigureReferenceIn:
        encoded = base64.b64encode(payload).decode("ascii")
        return ra.FigureReferenceIn(
            filename="item20_source_crop_hd.png",
            data_url=f"data:image/png;base64,{encoded}",
            source_label="2024학년도 수능 물리학Ⅰ 20번",
            source_meta={
                "asset_mode": "source_crop_hd",
                "source_pdf": "PDF/p1_2024_11.pdf",
                "page_no": 4,
                "bbox": [463.8, 718.261, 738.0, 798.661],
                "dpi": 600,
                "width_px": 2285,
                "height_px": 668,
                "aspect_ratio": 2285 / 668,
                "source_hash": source_hash or hashlib.sha256(payload).hexdigest(),
            },
        )

    def test_source_crop_hd_is_selected_automatically_for_reconstruction(self):
        # Given: a reconstruction session and a high-resolution source crop.
        ra.update_settings(self.sid, ra.SettingsIn(purpose_mode="reconstruct"))
        payload = b"exact-source-crop-bytes"

        # When: the source reference enters through the real reference API.
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.routes_authoring.hwppalette_provider.photo_dir", return_value=Path(tmp)
        ), patch("app.routes_authoring.hwppalette_provider.register_photo_dir"):
            session = ra.add_figure_reference(self.sid, self._body(payload))
            primary = ra.get_figure_image(self.sid)
            asset = ra.get_figure_asset_image(
                self.sid, session["figure"]["assets"][0]["id"]
            )

            # Then: editor and immediate-preview routing resolve the same exact bytes.
            figure = session["figure"]
            routed = figure["assets"][0]
            self.assertEqual(figure["provider"], "source_crop_hd")
            self.assertEqual(figure["options"]["provider"], "source_crop_hd")
            self.assertEqual(routed["asset_mode"], "source_crop_hd")
            self.assertEqual(routed["asset_role"], "original_source")
            self.assertEqual(routed["source_pdf"], "PDF/p1_2024_11.pdf")
            self.assertEqual(routed["page_no"], 4)
            self.assertEqual(routed["bbox"], [463.8, 718.261, 738.0, 798.661])
            self.assertEqual(routed["dpi"], 600)
            self.assertEqual((routed["width_px"], routed["height_px"]), (2285, 668))
            self.assertAlmostEqual(routed["aspect_ratio"], 2285 / 668)
            self.assertEqual(routed["source_hash"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(routed["fivee_project_path"], "")
            self.assertEqual(Path(primary.path), Path(asset.path))
            self.assertEqual(Path(primary.path).read_bytes(), payload)

    def test_source_crop_hd_prohibits_generation_import_and_edit_routes(self):
        # Given: an automatically routed original source crop.
        ra.update_settings(self.sid, ra.SettingsIn(purpose_mode="reconstruct"))
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.routes_authoring.hwppalette_provider.photo_dir", return_value=Path(tmp)
        ), patch("app.routes_authoring.hwppalette_provider.register_photo_dir"):
            session = ra.add_figure_reference(self.sid, self._body(b"locked-source"))
            asset_id = session["figure"]["assets"][0]["id"]

            # When/Then: no 5E, raster/ImageGen, replacement import, or edit action is accepted.
            blocked_calls = (
                lambda: ra.update_figure_options(
                    self.sid, ra.FigureOptionsIn(provider="fivee_assets")
                ),
                lambda: ra.figure_action(self.sid, "draw"),
                lambda: ra.figure_action(self.sid, "create"),
                lambda: ra.import_figure_image(
                    self.sid,
                    ra.FigureImageImportIn(
                        filename="replacement.png",
                        data_url="data:image/png;base64," + base64.b64encode(b"replacement").decode(),
                    ),
                ),
                lambda: ra.figure_asset_action(self.sid, asset_id, "edit"),
                lambda: ra.delete_figure_reference(
                    self.sid, session["figure"]["references"][0]["id"]
                ),
            )
            for call in blocked_calls:
                with self.subTest(call=call), self.assertRaises(HTTPException) as raised:
                    call()
                self.assertEqual(raised.exception.status_code, 409)

    def test_source_crop_hd_rejects_bytes_that_do_not_match_source_hash(self):
        # Given: reconstruction metadata whose hash does not describe the supplied crop.
        ra.update_settings(self.sid, ra.SettingsIn(purpose_mode="reconstruct"))

        # When/Then: the reference boundary rejects it before it becomes an asset.
        with self.assertRaises(HTTPException) as raised:
            ra.add_figure_reference(self.sid, self._body(b"crop", source_hash="0" * 64))
        self.assertEqual(raised.exception.status_code, 422)

    def test_source_crop_hd_metadata_columns_are_migrated(self):
        # Given/When: the application database schema is initialized.
        with db.connect() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(authoring_figure_asset)")
            }

        # Then: every provenance and mode discriminator has a durable column.
        self.assertTrue({
            "asset_mode", "asset_role", "source_pdf", "page_no", "bbox_json", "dpi",
            "width_px", "height_px", "aspect_ratio", "source_hash",
        }.issubset(columns))

    def test_source_crop_hd_routes_when_reconstruction_is_selected_after_reference(self):
        # Given: the extractor sidecar is attached before the user chooses reconstruction.
        body = self._body(b"reference-first")
        body.source_meta["source_hash"] = "1" * 64
        body.source_meta["asset_hash"] = hashlib.sha256(b"reference-first").hexdigest()
        body.source_meta["image_path"] = "assets/item_figures/item20_source_crop_hd.png"
        ra.add_figure_reference(self.sid, body)

        # When: the authoring purpose changes to reconstruction.
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.routes_authoring.hwppalette_provider.photo_dir", return_value=Path(tmp)
        ), patch("app.routes_authoring.hwppalette_provider.register_photo_dir"):
            session = ra.update_settings(
                self.sid, ra.SettingsIn(purpose_mode="reconstruct")
            )

            # Then: sidecar extras are tolerated and no method choice is required.
            self.assertEqual(session["figure"]["provider"], "source_crop_hd")
            self.assertEqual(session["figure"]["status"], "confirmed")
            self.assertEqual(session["figure"]["assets"][0]["source_hash"], "1" * 64)

    def test_source_crop_hd_frontend_hides_method_choice_and_edit_actions(self):
        # Given: the vanilla authoring UI source.
        source = (
            Path(__file__).parents[1] / "static" / "js" / "authoring.js"
        ).read_text(encoding="utf-8")

        # When/Then: the routing discriminator drives the locked original-use state.
        self.assertIn('const sourceCropMode = figure.provider === "source_crop_hd";', source)
        self.assertIn('$("auFigureProvider").disabled = sourceCropMode;', source)
        self.assertIn('$("auFigureDraw").classList.toggle("hidden", sourceCropMode);', source)
        self.assertIn('preview.setAttribute("aria-disabled", sourceCropMode ? "true" : "false");', source)

    def test_real_item20_sidecar_routes_exact_asset_bytes(self):
        # Given: the extractor's canonical item-20 PNG and sidecar.
        root = Path(__file__).parents[1]
        metadata = json.loads(
            (root / "assets/item_figures/item20_source_crop_hd.json").read_text(encoding="utf-8")
        )
        payload = (root / metadata["image_path"]).read_bytes()
        encoded = base64.b64encode(payload).decode("ascii")
        ra.update_settings(self.sid, ra.SettingsIn(purpose_mode="reconstruct"))

        # When: it enters the same API as a real authoring reference.
        with tempfile.TemporaryDirectory() as tmp, patch(
            "app.routes_authoring.hwppalette_provider.photo_dir", return_value=Path(tmp)
        ), patch("app.routes_authoring.hwppalette_provider.register_photo_dir"):
            session = ra.add_figure_reference(
                self.sid,
                ra.FigureReferenceIn(
                    filename=Path(metadata["image_path"]).name,
                    data_url=f"data:image/png;base64,{encoded}",
                    source_meta=metadata,
                ),
            )
            served = ra.get_figure_image(self.sid)

            # Then: source identity persists while served bytes match asset_hash exactly.
            asset = session["figure"]["assets"][0]
            self.assertEqual(asset["source_hash"], metadata["source_hash"])
            self.assertEqual(hashlib.sha256(Path(served.path).read_bytes()).hexdigest(), metadata["asset_hash"])

    def test_item20_reconstruction_record_selects_canonical_source_crop(self):
        # Given/When: the checked-in item-20 reconstruction seed is loaded.
        root = Path(__file__).parents[1]
        item = json.loads(
            (root / "app/seed/reconstructions/p1_2024_11_item20.json").read_text(encoding="utf-8")
        )
        sidecar = json.loads(
            (root / "assets/item_figures/item20_source_crop_hd.json").read_text(encoding="utf-8")
        )

        # Then: it points to the exact source crop and carries the routing metadata.
        self.assertEqual(item["figure"]["provider"], "source_crop_hd")
        self.assertEqual(item["figure"]["rendered_image_path"], sidecar["image_path"])
        for field in (
            "source_pdf", "page_no", "bbox", "dpi", "width_px", "height_px",
            "aspect_ratio", "source_hash",
        ):
            self.assertEqual(item["figure"][field], sidecar[field])


if __name__ == "__main__":
    unittest.main()
