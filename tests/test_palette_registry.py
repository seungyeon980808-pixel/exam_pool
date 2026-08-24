import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.integrations import palette_registry as registry


def copy_seed(root: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter" / "seed_data"
    target = root / "seed"
    shutil.copytree(source, target)
    return target


def make_hwpal(*, label="수능AI실제직접형", name="직접형", payload=b"new-hwp",
               slot_names=None, slot_options=None, extra=None):
    slot_names = slot_names or ["문항번호", "발문"]
    slot_options = slot_options or [{} for _ in slot_names]
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
                "category": "템플릿", "name": name, "label": label,
                "file": "direct.hwp", "slot_count": len(slot_names),
                "slot_names": slot_names, "slot_options": slot_options,
                "origin_id": "old-id",
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

    def test_실행_캐시는_같은_원본의_정규화_계약이_바뀌어도_갱신한다(self):
        installed = registry.install_hwpal(make_hwpal(), "수능.hwpal", "suneung")
        before = registry._active_digest()
        normalized = (
            self.root / "data" / "typesetting_palettes" / "packages"
            / installed["digest"] / "normalized.json"
        )
        record = json.loads(normalized.read_text(encoding="utf-8"))
        record["items"][0]["slot_names"] = ["문항번호", "문두"]
        normalized.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

        after = registry._active_digest()

        self.assertNotEqual(before, after)

    def test_물감별_시험_결과를_저장하고_재등록해도_유지한다(self):
        content = make_hwpal()
        installed = registry.install_hwpal(content, "수능.hwpal", "suneung")
        result = registry.save_item_test(installed["id"], 0, "passed", "정상")
        self.assertEqual(result["state"], "passed")
        listed = registry.list_palettes()["packages"][0]
        self.assertEqual(listed["item_tests"]["템플릿:수능AI실제직접형"]["message"], "정상")

        registry.install_hwpal(content, "수능.hwpal", "suneung")
        listed = registry.list_palettes()["packages"][0]
        self.assertEqual(listed["item_tests"]["템플릿:수능AI실제직접형"]["state"], "passed")

    def test_활성화하지_않은_양식으로는_물감_시험을_막는다(self):
        installed = registry.install_hwpal(make_hwpal(), "수능.hwpal", "suneung")
        with self.assertRaisesRegex(registry.PalettePackageError, "먼저 학교"):
            registry.package_item(installed["id"], 0, "school")

    def test_derived_suneung_templates_are_available_without_an_active_palette(self):
        self.assertIsNone(registry._load_registry().get("active", {}).get("suneung"))
        item = registry.active_template("suneung", "수능정답1대사진5선지")
        self.assertIsNotNone(item)
        self.assertEqual(item["slot_names"][2], "사진1")
        hapdap = registry.active_template("suneung", "수능합답2대사진5선지")
        self.assertIsNotNone(hapdap)
        self.assertEqual(hapdap["slot_count"], 13)
        exact = registry.active_template("suneung", "수능원문1대사진")
        self.assertIsNotNone(exact)
        self.assertEqual(exact["slot_names"], ["사진1"])

    def test_bundled_editable_comparison_template_is_available_without_an_active_palette(self):
        comparison = registry.active_template("suneung", "수능AI실제비교선지형")

        self.assertEqual(
            comparison["slot_names"],
            ["문항번호", "발문", "질문", "표머리", "선지1", "선지2", "선지3", "선지4", "선지5"],
        )

    def test_bundled_hapdap_template_is_available_without_an_active_palette(self):
        item = registry.active_template("suneung", "수능AI실제합답형")

        self.assertEqual(
            item["slot_names"],
            ["문항번호", "발문", "질문", "보기ㄱ", "보기ㄴ", "보기ㄷ",
             "선지1", "선지2", "선지3", "선지4", "선지5"],
        )

    def test_bundled_direct_template_is_available_without_an_active_palette(self):
        item = registry.active_template("suneung", "수능AI실제직접형")

        self.assertEqual(
            item["slot_names"],
            ["문항번호", "발문", "질문", "배점",
             "선지1", "선지2", "선지3", "선지4", "선지5"],
        )

    def test_활성_물감을_호출_라벨로_찾는다(self):
        registry.install_hwpal(make_hwpal(label="사용자합답양식"),
                               "수능.hwpal", "suneung")
        item = registry.active_template("suneung", "사용자합답양식")
        self.assertIsNotNone(item)
        self.assertEqual(item["slot_names"], ["문항번호", "발문"])
        self.assertIsNone(registry.active_template("school", "사용자합답양식"))

    def test_슬롯별_자간맞춤_옵션을_실행_라이브러리까지_보존한다(self):
        content = make_hwpal(slot_options=[{}, {"spacing_fit": True}])
        registry.install_hwpal(content, "수능.hwpal", "suneung")
        seed = copy_seed(self.root)
        runtime = self.root / "runtime"
        registry.materialize_active(runtime, seed, force=True)
        library = json.loads((runtime / "library.json").read_text(encoding="utf-8"))
        installed = next(item for item in library["템플릿"] if item["id"].startswith("hwpal:"))
        self.assertEqual(installed["slot_options"], [{}, {"spacing_fit": True}])

    def test_HwpPalette_재등록_접미사를_기존_호출_라벨로_정규화한다(self):
        content = make_hwpal(
            name="분리양식합답5선지 (2)",
            label="분리양식합답5선지2",
            slot_names=["ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", ""],
        )

        metadata, _ = registry.inspect_hwpal(content, "수능양식개발(0817).hwpal")

        item = metadata["items"][0]
        self.assertEqual(item["name"], "분리양식합답5선지")
        self.assertEqual(item["label"], "분리양식합답5선지")
        self.assertEqual(item["slot_names"], ["ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5"])

    def test_새_실험형_호출_라벨을_표준_라벨로_정규화한다(self):
        content = make_hwpal(
            name="수능 AI 실제 실험형 (문두 분리)",
            label="수능AI실제실험형2",
            slot_names=[
                "문항번호", "문두", "실험내용", "사진1", "표", "발문",
                "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
            ],
        )

        metadata, _ = registry.inspect_hwpal(content, "수능양식개발(0817).hwpal")

        self.assertEqual(metadata["items"][0]["label"], "수능AI실제실험형")
        self.assertEqual(metadata["contract"], {"수능AI실제실험형": 14})

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
        seed = copy_seed(self.root)
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
        labels = [item["label"] for item in library["템플릿"]]
        expected_labels = {
            "수능AI실제직접형",
            "수능AI실제합답형",
            *registry._DERIVED_TEMPLATES,
        }
        self.assertEqual(len(labels), len(set(labels)), "materialized labels must be unique")
        self.assertEqual(set(by_label), expected_labels)
        replaced = by_label["수능AI실제직접형"]
        self.assertTrue(replaced["id"].startswith("hwpal:"))
        self.assertEqual((runtime / "fragments" / replaced["file"]).read_bytes(), b"corrected")
        self.assertEqual(by_label["수능AI실제합답형"]["id"], "other")
        for label, (filename, slot_names) in registry._DERIVED_TEMPLATES.items():
            derived = by_label[label]
            self.assertEqual(derived["file"], f"exampool_{filename}")
            self.assertEqual(derived["slot_names"], slot_names)
            payload = (runtime / "fragments" / derived["file"]).read_bytes()
            self.assertTrue(payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"))

    def test_비활성화하면_내장_기본값으로_돌아간다(self):
        installed = registry.install_hwpal(make_hwpal(), "수능.hwpal", "suneung")
        registry.deactivate("suneung")
        self.assertEqual(registry.list_palettes()["active"], {})
        registry.activate(installed["id"], "school")
        self.assertEqual(registry.list_palettes()["active"]["school"], installed["id"])


if __name__ == "__main__":
    unittest.main()
