import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.integrations import palette_registry as registry


def make_hwpal(*, label="수능AI실제직접형", payload=b"new-hwp",
               slot_names=None, extra=None):
    slot_names = slot_names or ["문항번호", "발문"]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("chip.json", json.dumps({
            "chip_version": 1, "name": "수능 수정본", "made_with": "HwpPalette test",
        }, ensure_ascii=False))
        zf.writestr("exam.json", json.dumps({
            "schema_version": 1, "kind": "exam_palette", "layout_style": "suneung",
        }))
        zf.writestr("library.json", json.dumps({
            "version": 1,
            "items": [{
                "category": "템플릿", "name": "직접형", "label": label,
                "file": "direct.hwp", "slot_count": len(slot_names),
                "slot_names": slot_names, "origin_id": "old-id",
            }],
        }, ensure_ascii=False))
        zf.writestr("fragments/direct.hwp", payload)
        if extra:
            zf.writestr(extra[0], extra[1])
    return stream.getvalue()


class PaletteRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_patch = patch.object(registry, "data_dir", return_value=self.root / "data")
        self.data_patch.start()

    def tearDown(self):
        self.data_patch.stop()
        self.temp.cleanup()

    def test_hwpal을_검사하고_등록과_활성화를_한번에_한다(self):
        out = registry.install_hwpal(make_hwpal(), "수능.hwpal", "suneung")
        self.assertTrue(out["installed"])
        self.assertEqual(out["active_for"], ["suneung"])
        self.assertEqual(out["contract"], {"수능AI실제직접형": 2})
        listed = registry.list_palettes()
        self.assertEqual(len(listed["packages"]), 1)
        self.assertEqual(listed["active"]["suneung"], out["id"])

    def test_같은_파일을_다시_등록해도_한_버전만_남는다(self):
        content = make_hwpal()
        registry.install_hwpal(content, "수능.hwpal", "suneung")
        registry.install_hwpal(content, "수능.hwpal", "suneung")
        self.assertEqual(len(registry.list_palettes()["packages"]), 1)

    def test_상위_폴더로_빠져나가는_zip은_거부한다(self):
        content = make_hwpal(extra=("../escape.hwp", b"bad"))
        with self.assertRaisesRegex(registry.PalettePackageError, "안전하지 않은"):
            registry.inspect_hwpal(content, "bad.hwpal")

    def test_슬롯_개수와_이름이_다르면_거부한다(self):
        content = make_hwpal()
        src = io.BytesIO(content)
        out = io.BytesIO()
        with zipfile.ZipFile(src) as before, zipfile.ZipFile(out, "w") as after:
            for info in before.infolist():
                data = before.read(info)
                if info.filename == "library.json":
                    value = json.loads(data)
                    value["items"][0]["slot_count"] = 9
                    data = json.dumps(value, ensure_ascii=False).encode()
                after.writestr(info.filename, data)
        with self.assertRaisesRegex(registry.PalettePackageError, "슬롯 개수"):
            registry.inspect_hwpal(out.getvalue(), "bad.hwpal")

    def test_활성_팔레트는_같은_라벨만_교체한다(self):
        seed = self.root / "seed"
        (seed / "fragments").mkdir(parents=True)
        (seed / "fragments" / "base.hwp").write_bytes(b"base")
        (seed / "fragments" / "other.hwp").write_bytes(b"other")
        (seed / "library.json").write_text(json.dumps({
            "서식": [], "문자": [], "양식": [],
            "템플릿": [
                {"id": "base", "name": "기본 직접형", "label": "수능AI실제직접형",
                 "file": "base.hwp", "slot_count": 2, "slot_names": ["문항번호", "발문"]},
                {"id": "other", "name": "합답형", "label": "수능AI실제합답형",
                 "file": "other.hwp", "slot_count": 1, "slot_names": ["문항번호"]},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        registry.install_hwpal(make_hwpal(payload=b"corrected"), "수능.hwpal", "suneung")

        runtime = self.root / "runtime"
        registry.materialize_active(runtime, seed, force=True)
        library = json.loads((runtime / "library.json").read_text(encoding="utf-8"))
        by_label = {item["label"]: item for item in library["템플릿"]}
        self.assertEqual(set(by_label), {"수능AI실제직접형", "수능AI실제합답형"})
        replaced = by_label["수능AI실제직접형"]
        self.assertTrue(replaced["id"].startswith("hwpal:"))
        self.assertEqual((runtime / "fragments" / replaced["file"]).read_bytes(), b"corrected")
        self.assertEqual(by_label["수능AI실제합답형"]["id"], "other")

    def test_비활성화하면_내장_기본값으로_돌아간다(self):
        installed = registry.install_hwpal(make_hwpal(), "수능.hwpal", "suneung")
        registry.deactivate("suneung")
        self.assertEqual(registry.list_palettes()["active"], {})
        registry.activate(installed["id"], "school")
        self.assertEqual(registry.list_palettes()["active"]["school"], installed["id"])


if __name__ == "__main__":
    unittest.main()
