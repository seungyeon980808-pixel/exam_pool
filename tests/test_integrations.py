# -*- coding: utf-8 -*-
import json
import sys
import subprocess
import tempfile
import importlib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app import db
from app.integrations import palette_registry
from app.integrations.hwppalette import HwpPaletteError, HwpPaletteProvider
from app.routes_integrations import (_evidence_summary, _paint_test_markdown, integration_status,
                                     preview_question, review_report, typeset_set)
from app.routes_question import ChoiceIn, QuestionIn


class TestHwpPaletteContract(unittest.TestCase):
    def test_nested_table_is_inline_and_uses_the_parent_cell_width(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            hwp_engine = importlib.import_module("hwp_palette.hwp.hwp_engine")

        hwp = MagicMock()
        hwp.GetPos.return_value = (7, 0, 0)
        hwp.get_col_width.return_value = 52.0
        hwp.MiliToHwpUnit.side_effect = lambda value: round(value * 100)
        table_creation = hwp.HParameterSet.HTableCreation

        with patch.object(hwp_engine, "hwp", hwp):
            hwp_engine.create_table_autofit(5, 3)

        self.assertEqual(table_creation.WidthType, 2)
        self.assertEqual(table_creation.WidthValue, 4160)
        self.assertTrue(table_creation.TableProperties.TreatAsChar)
        self.assertEqual(table_creation.TableProperties.Width, 4160)
        hwp.CreateSet.assert_called_once_with("Table")
        inline_properties = hwp.CreateSet.return_value
        inline_properties.SetItem.assert_called_once_with("TreatAsChar", True)

    def test_nested_table_exit_returns_to_its_parent_cell(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            hwp_engine = importlib.import_module("hwp_palette.hwp.hwp_engine")

        hwp = MagicMock()
        positions = iter(((9, 0, 0), (7, 0, 1)))
        hwp.GetPos.side_effect = lambda: next(positions)

        with patch.object(hwp_engine, "hwp", hwp):
            reached = hwp_engine.exit_table(parent_list_id=7)

        self.assertTrue(reached)
        self.assertEqual(
            hwp.HAction.Run.call_args_list,
            [unittest.mock.call("Cancel"), unittest.mock.call("CloseEx"),
             unittest.mock.call("MoveRight")],
        )

    def test_experiment_picture_uses_compact_material_box_frame(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        hwp = MagicMock()
        hwp.get_col_width.return_value = 108.0
        hwp.get_row_height.return_value = 87.0
        hwp.get_cell_margin.return_value = {}
        hwp.get_col_num.return_value = 1
        hwp.get_row_num.return_value = 1
        hwp.MiliToHwpUnit.side_effect = lambda value: round(value * 100)
        picture = MagicMock()
        hwp.insert_picture.return_value = picture

        with patch.object(engine.hwp_engine, "in_table", return_value=True), \
             patch.object(engine, "_image_size_mm", return_value=(80.0, 160.0)):
            engine._insert_picture_sized(
                hwp, "experiment.png", max_bounds_mm=(43.0, 43.5),
            )

        picture.Properties.SetItem.assert_any_call("Width", 2175)
        picture.Properties.SetItem.assert_any_call("Height", 4350)

    def test_embedded_runtime_deletes_only_the_table_containing_unused_bogi(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        hwp = MagicMock()
        hwp.ParentCtrl.CtrlID = "tbl"
        with patch.object(engine, "_h", return_value=hwp), \
             patch.object(engine, "find_text", return_value=True):
            removed = engine.delete_table_containing_text("<보 기>")

        self.assertTrue(removed)
        hwp.delete_ctrl.assert_called_once_with(hwp.ParentCtrl)

    def test_direct_large_photo_contract_collapses_only_empty_bogi_slots(self):
        from app.integrations.hwppalette_runner import _needs_empty_bogi_collapse

        direct = "\\수능합답1대사진5선지\\\n20\nstem\n\\photo\\\nask\n-\n-\n-\n①\n②"
        genuine_bogi = direct.replace("\n-\n-\n-\n", "\nㄱ\nㄴ\nㄷ\n")

        self.assertTrue(_needs_empty_bogi_collapse(direct))
        self.assertFalse(_needs_empty_bogi_collapse(genuine_bogi))

    def test_direct_large_photo_single_item_repair_does_not_mutate_batch_document(self):
        from app.integrations.hwppalette_runner import _needs_empty_bogi_collapse

        label = "\\\uc218\ub2a5\ud569\ub2f51\ub300\uc0ac\uc9c45\uc120\uc9c0\\"
        direct = f"{label}\n20\nstem\n\\photo\\\nask\n-\n-\n-\nchoices"
        batch = direct + f"\n\n{label}\n19\nsecond stem\n\\photo2\\\nask\n-\n-\n-\nchoices"

        self.assertFalse(_needs_empty_bogi_collapse(batch))

    def test_direct_question_paragraphs_use_word_boundary_wrapping(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        hwp = MagicMock()
        hwp.HParameterSet.HParaShape.BreakNonLatinWord = 1
        with patch.object(engine, "_h", return_value=hwp), \
             patch.object(engine, "find_text", return_value=True):
            self.assertTrue(engine.set_paragraph_word_boundary_wrap(
                "가만히 놓았더니", character_ratio=90,
            ))
        self.assertEqual(hwp.HParameterSet.HParaShape.BreakNonLatinWord, 0)
        self.assertEqual(hwp.HParameterSet.HParaShape.Condense, 20)
        hwp.MoveParaBegin.assert_called_once_with()
        hwp.MoveSelParaEnd.assert_called_once_with()
        hwp.set_font.assert_called_once_with(Ratio=90)
        hwp.HAction.Execute.assert_called_once_with(
            "ParagraphShape", hwp.HParameterSet.HParaShape.HSet,
        )

    def test_filled_text_slots_apply_korean_word_boundary_to_the_destination_paragraph(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        hwp = MagicMock()
        hwp.GetPos.return_value = (0, 0, 0)
        with patch.object(engine, "_h", return_value=hwp), \
             patch.object(engine, "find_text", return_value=True), \
             patch.object(engine, "insert_plain") as insert_plain, \
             patch.object(engine, "strip_slot_markers"), \
             patch.object(engine, "_set_current_paragraph_word_boundary_wrap") as set_wrap:
            filled, wanted = engine.fill_slots(
                (0, 0, 0), ["빛의 스펙트럼선"], end_para=1, slot_count=1,
                slot_names=["보기"],
            )

        self.assertEqual((filled, wanted), (1, 1))
        insert_plain.assert_called_once_with("빛의 스펙트럼선")
        set_wrap.assert_called_once_with()

    def test_current_destination_paragraph_uses_eojeol_wrap_and_safe_space_condense(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        hwp = MagicMock()
        hwp.HParameterSet.HParaShape.BreakNonLatinWord = 1
        hwp.HParameterSet.HParaShape.Condense = 0
        with patch.object(engine, "_h", return_value=hwp):
            engine._set_current_paragraph_word_boundary_wrap()

        self.assertEqual(hwp.HParameterSet.HParaShape.BreakNonLatinWord, 0)
        self.assertEqual(hwp.HParameterSet.HParaShape.Condense, 20)
        hwp.HAction.Execute.assert_called_once_with(
            "ParagraphShape", hwp.HParameterSet.HParaShape.HSet,
        )

    def test_large_experiment_template_starts_on_a_fresh_page(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        hwp = MagicMock()
        ops = [
            ("line", "앞 문항", None),
            ("template", {"label": "수능AI실제실험형"}, []),
        ]
        with patch.object(engine, "_h", return_value=hwp), \
             patch.object(engine, "insert_plain"), \
             patch.object(engine, "_find_template_marker", return_value=False):
            engine.execute_library_plan(ops, lambda _item: "fragment.hwp")

        self.assertEqual(
            [call.args[0] for call in hwp.HAction.Run.call_args_list],
            ["BreakPara", "BreakPage"],
        )

    def test_long_atomic_boxed_question_starts_on_a_fresh_page(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
            parser = importlib.import_module("hwp_palette.model.parser")

        hwp = MagicMock()
        boxed_passage = parser.MultiLine((
            "문두",
            parser.Table(1, 1, (("가" * 420,),)),
        ))
        ops = [
            ("line", "앞 문항", None),
            ("template", {"label": "수능AI실제합답형"}, [boxed_passage]),
        ]
        with patch.object(engine, "_h", return_value=hwp), \
             patch.object(engine, "insert_plain"), \
             patch.object(engine, "_find_template_marker", return_value=False):
            engine.execute_library_plan(ops, lambda _item: "fragment.hwp")

        self.assertEqual(
            [call.args[0] for call in hwp.HAction.Run.call_args_list],
            ["BreakPara", "BreakPage"],
        )

    def test_first_experiment_template_does_not_skip_an_empty_column(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        hwp = MagicMock()
        ops = [("template", {"label": "수능AI실제실험형"}, [])]
        with patch.object(engine, "_h", return_value=hwp), \
             patch.object(engine, "insert_plain"), \
             patch.object(engine, "_find_template_marker", return_value=False):
            engine.execute_library_plan(ops, lambda _item: "fragment.hwp")

        hwp.HAction.Run.assert_not_called()

    def test_parser_moves_an_unfinished_question_clause_after_intervening_photo_slots(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            parser = importlib.import_module("hwp_palette.model.parser")
        item = {
            "slot_count": 4,
            "slot_names": ["문항번호", "문두", "사진1", "발문"],
        }
        lookup = {
            "질문양식": ("템플릿", item),
            "실제그림": ("사진", {"path": "actual.png"}),
        }
        markdown = "\n".join([
            "\\질문양식\\",
            "5",
            r"그림은 위치 \수식{x}를 나타낸 것이다. 이에 대한 설명으로 옳은 것을 고른",
            "\\실제그림\\",
            "것은? [3점]",
        ])

        ops, warnings = parser.build_library_plan(markdown, lookup)

        self.assertEqual(warnings, [])
        fills = ops[0][2]
        passage = "".join(segment.get("text", "") for segment in fills[1])
        self.assertEqual(passage, "그림은 위치 를 나타낸 것이다.")
        self.assertEqual(fills[3], "이에 대한 설명으로 옳은 것을 고른 것은? [3점]")

    def test_parser_leaves_an_already_complete_post_photo_question_unchanged(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            parser = importlib.import_module("hwp_palette.model.parser")
        item = {
            "slot_count": 4,
            "slot_names": ["문항번호", "문두", "사진1", "발문"],
        }
        lookup = {
            "질문양식": ("템플릿", item),
            "실제그림": ("사진", {"path": "actual.png"}),
        }
        markdown = "\n".join([
            "\\질문양식\\", "5", "그림은 위치를 나타낸 것이다.",
            "\\실제그림\\", "옳은 것은? [3점]",
        ])

        ops, warnings = parser.build_library_plan(markdown, lookup)

        self.assertEqual(warnings, [])
        fills = ops[0][2]
        self.assertEqual(fills[1], "그림은 위치를 나타낸 것이다.")
        self.assertEqual(fills[3], "옳은 것은? [3점]")

    def test_table_picture_uses_contain_fit_for_wide_tall_and_square_sources(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        for source_size, expected_size in [
            ((200.0, 50.0), (100.0, 25.0)),
            ((50.0, 200.0), (15.0, 60.0)),
            ((80.0, 80.0), (60.0, 60.0)),
        ]:
            with self.subTest(source_size=source_size):
                hwp = MagicMock()
                hwp.get_col_width.return_value = 100.0
                hwp.get_row_height.return_value = 60.0
                hwp.MiliToHwpUnit.side_effect = lambda value: round(value * 100)
                picture = MagicMock()
                hwp.insert_picture.return_value = picture
                with patch.object(engine.hwp_engine, "in_table", return_value=True), \
                     patch.object(engine, "_image_size_mm", return_value=source_size):
                    engine._insert_picture_sized(hwp, "figure.png")

                width, height = expected_size
                hwp.insert_picture.assert_called_once_with(
                    "figure.png", treat_as_char=True, embedded=True,
                    sizeoption=0,
                )
                picture.Properties.SetItem.assert_any_call(
                    "Width", hwp.MiliToHwpUnit(round(width, 2)),
                )
                picture.Properties.SetItem.assert_any_call(
                    "Height", hwp.MiliToHwpUnit(round(height, 2)),
                )

    def test_generated_table_uses_eighty_percent_of_the_available_width(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            hwp_engine = importlib.import_module("hwp_palette.hwp.hwp_engine")
        hwp = MagicMock()
        hwp.MiliToHwpUnit.side_effect = lambda value: round(value * 100)
        hwp.CurSelectedCtrl._com_obj = MagicMock()

        with patch.object(hwp_engine, "hwp", hwp), \
             patch.object(hwp_engine, "S", {"layout": {"column_width_mm": 100.0}}), \
             patch.object(hwp_engine, "in_table", return_value=False):
            hwp_engine.create_table_autofit(2, 2)

        creation = hwp.HParameterSet.HTableCreation
        self.assertEqual(creation.WidthType, 2)
        self.assertEqual(creation.WidthValue, 8000)

    def test_nested_generated_table_uses_eighty_percent_of_the_host_cell(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            hwp_engine = importlib.import_module("hwp_palette.hwp.hwp_engine")
        hwp = MagicMock()
        hwp.get_col_width.return_value = 50.0
        hwp.MiliToHwpUnit.side_effect = lambda value: round(value * 100)
        hwp.CurSelectedCtrl._com_obj = MagicMock()

        with patch.object(hwp_engine, "hwp", hwp), \
             patch.object(hwp_engine, "in_table", return_value=True):
            hwp_engine.create_table_autofit(2, 2)

        creation = hwp.HParameterSet.HTableCreation
        self.assertEqual(creation.WidthValue, 4000)

    def test_generated_table_centers_every_cell_value(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        hwp = MagicMock()
        hwp.GetPos.return_value = (0, 0, 0)

        with patch.object(engine, "_h", return_value=hwp), \
             patch.object(engine.hwp_engine, "create_table_autofit"), \
             patch.object(engine.hwp_engine, "exit_table"), \
             patch.object(engine, "insert_plain"):
            engine.insert_table(2, 2, [["거리", "속력"], ["1", "4"]])

        centered = [
            call for call in hwp.HAction.Run.call_args_list
            if call.args == ("ParagraphShapeAlignCenter",)
        ]
        self.assertEqual(len(centered), 4)

    def test_table_picture_excludes_cell_inner_margins_from_contain_width(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        hwp = MagicMock()
        hwp.get_col_width.return_value = 100.0
        hwp.get_row_height.return_value = 60.0
        hwp.get_cell_margin.return_value = {
            "left": 2.0, "right": 2.0, "top": 0.5, "bottom": 0.5,
        }
        hwp.get_col_num.return_value = 2
        hwp.MiliToHwpUnit.side_effect = lambda value: round(value * 100)
        picture = MagicMock()
        hwp.insert_picture.return_value = picture
        with patch.object(engine.hwp_engine, "in_table", return_value=True), \
             patch.object(engine, "_image_size_mm", return_value=(200.0, 100.0)):
            engine._insert_picture_sized(hwp, "figure.png")

        picture.Properties.SetItem.assert_any_call("Width", 8900)
        picture.Properties.SetItem.assert_any_call("Height", 4450)

    def test_single_cell_picture_table_shrinks_to_contained_image_height(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        hwp = MagicMock()
        hwp.get_col_width.return_value = 100.0
        hwp.get_row_height.return_value = 60.0
        hwp.get_col_num.return_value = 1
        hwp.get_row_num.return_value = 1
        hwp.insert_picture.return_value = MagicMock()

        with patch.object(engine.hwp_engine, "in_table", return_value=True), \
             patch.object(engine, "_image_size_mm", return_value=(200.0, 50.0)):
            engine._insert_picture_sized(hwp, "wide.png")

        hwp.set_row_height.assert_called_once_with(27.0)

    def test_large_single_cell_grows_to_seventy_percent_readable_scale(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        hwp = MagicMock()
        row_height = [29.0]
        column_width = [61.0]
        hwp.get_col_width.side_effect = lambda: column_width[0]
        hwp.get_row_height.side_effect = lambda: row_height[0]
        hwp.get_cell_margin.return_value = {
            "left": 1.3, "right": 1.3, "top": 1.0, "bottom": 1.0,
        }
        hwp.get_col_num.return_value = 1
        hwp.get_row_num.return_value = 1
        hwp.insert_picture.return_value = MagicMock()
        hwp.MiliToHwpUnit.side_effect = lambda value: round(value * 100)
        hwp.set_row_height.side_effect = lambda value: row_height.__setitem__(0, value)
        hwp.set_col_width.side_effect = (
            lambda value, **_kwargs: column_width.__setitem__(0, value)
        )

        with patch.object(engine.hwp_engine, "in_table", return_value=True), \
             patch.object(engine, "_image_size_mm", return_value=(98.425, 44.662)):
            engine._insert_picture_sized(hwp, "q18.png")

        calls = hwp.insert_picture.return_value.Properties.SetItem.call_args_list
        width = calls[0].args[1]
        height = calls[1].args[1]
        self.assertGreaterEqual(width / 100 / 98.425, 0.70)
        self.assertGreaterEqual(height / 100 / 44.662, 0.70)

    def test_small_single_cell_grows_to_seventy_percent_readable_scale(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        hwp = MagicMock()
        row_height = [14.0]
        column_width = [43.0]
        hwp.get_col_width.side_effect = lambda: column_width[0]
        hwp.get_row_height.side_effect = lambda: row_height[0]
        hwp.get_cell_margin.return_value = {
            "left": 1.0, "right": 1.0, "top": 1.0, "bottom": 1.0,
        }
        hwp.get_col_num.return_value = 1
        hwp.get_row_num.return_value = 1
        hwp.insert_picture.return_value = MagicMock()
        hwp.MiliToHwpUnit.side_effect = lambda value: round(value * 100)
        hwp.set_row_height.side_effect = lambda value: row_height.__setitem__(0, value)
        hwp.set_col_width.side_effect = (
            lambda value, **_kwargs: column_width.__setitem__(0, value)
        )

        with patch.object(engine.hwp_engine, "in_table", return_value=True), \
             patch.object(engine, "_image_size_mm", return_value=(48.4725, 21.1875)):
            engine._insert_picture_sized(hwp, "q12.png")

        calls = hwp.insert_picture.return_value.Properties.SetItem.call_args_list
        width = calls[0].args[1]
        height = calls[1].args[1]
        self.assertGreaterEqual(width / 100 / 48.4725, 0.70)
        self.assertGreaterEqual(height / 100 / 21.1875, 0.70)
        hwp.TableCellAlignCenterTop.assert_called_once_with()

    def test_pdf_crop_sidecar_is_authoritative_for_native_print_size(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "q18.png"
            Image.new("L", (1163, 529), "white").save(image_path)
            image_path.with_suffix(".json").write_text(json.dumps({
                "bbox": [112.8, 755.701, 391.8, 882.301],
            }), encoding="utf-8")

            width_mm, height_mm = engine._image_size_mm(image_path)

        self.assertAlmostEqual(width_mm, 279.0 * 25.4 / 72.0, places=3)
        self.assertAlmostEqual(height_mm, 126.6 * 25.4 / 72.0, places=3)

    def test_exam_style_conversion_cannot_change_table_picture_aspect(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        hwp = MagicMock()
        hwp.get_col_width.return_value = 100.0
        hwp.get_row_height.return_value = 60.0
        hwp.insert_picture.return_value = MagicMock()

        def image_size(path):
            return (200.0, 50.0) if str(path) == "figure.png" else (50.0, 200.0)

        def converted_path(_source, destination, *, style):
            self.assertEqual(style, "exam-clean")
            return Path(destination)

        with patch.object(engine.hwp_engine, "S", {"exam_image_style": "exam-clean"}), \
             patch.object(engine.hwp_engine, "in_table", return_value=True), \
             patch.object(engine, "_image_size_mm", side_effect=image_size), \
             patch("hwp_palette.hwp.exam_image.convert", side_effect=converted_path):
            engine._insert_picture_sized(hwp, "figure.png")

        hwp.insert_picture.assert_called_once_with(
            "figure.png", treat_as_char=True, embedded=True, sizeoption=0,
        )

    def test_trailing_template_page_is_deleted_only_when_it_has_no_item_content(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        hwp = MagicMock()
        hwp.PageCount = 2
        hwp.get_page_text.return_value = "2\n과학탐구영역\n(물리I)\n"
        with patch.object(engine, "_h", return_value=hwp):
            self.assertTrue(engine.delete_trailing_csat_form_page())
        hwp.goto_page.assert_called_once_with(2)
        hwp.DeletePage.assert_called_once_with()

    def test_trailing_template_page_cleanup_supports_multi_page_exam_sets(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")
        hwp = MagicMock()
        hwp.PageCount = 6
        hwp.get_page_text.return_value = "6\n과학탐구 영역\n(물리I)\n"
        with patch.object(engine, "_h", return_value=hwp):
            self.assertTrue(engine.delete_trailing_csat_form_page())
        hwp.goto_page.assert_called_once_with(6)
        hwp.DeletePage.assert_called_once_with()

    def test_embedded_equation_writer_uses_the_com_property_exposed_by_hwp_2022(self):
        # Given: the lowercase-only HEqEdit property exposed by the generated
        # HWP 2022 COM type library.
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        class EquationParameters:
            def __init__(self):
                self.HSet = object()
                self.string = ""
                self.BaseUnit = 0

        equation = EquationParameters()
        action = MagicMock()
        hwp = MagicMock()
        hwp.HAction = action
        hwp.HParameterSet.HEqEdit = equation
        hwp.GetPos.return_value = (0, 4, 9)

        # When: a stacked fraction is inserted through the embedded runtime.
        with patch.object(engine, "_h", return_value=hwp):
            engine._insert_equation(r"\frac{7}{17}h")

        # Then: the actual COM property receives Hancom's fraction script.
        self.assertEqual(equation.string, "{7} over {17}h")
        self.assertEqual(equation.BaseUnit, 1200)
        action.Execute.assert_called_once_with("EquationCreate", equation.HSet)
        hwp.SetPos.assert_called_once_with(0, 4, 10)
        action.Run.assert_called_once_with("Cancel")

    def test_embedded_equation_writer_matches_inline_formula_to_body_size(self):
        runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        with patch.object(sys, "path", [str(runtime), *sys.path]):
            engine = importlib.import_module("hwp_palette.hwp.engine_library")

        class EquationParameters:
            def __init__(self):
                self.HSet = object()
                self.string = ""
                self.BaseUnit = 0

        equation = EquationParameters()
        hwp = MagicMock()
        hwp.HAction = MagicMock()
        hwp.HParameterSet.HEqEdit = equation
        hwp.GetPos.return_value = (0, 4, 9)

        with patch.object(engine, "_h", return_value=hwp):
            engine._insert_equation("9h")

        self.assertEqual(equation.BaseUnit, 1150)

    def test_direct_hwp_registration_and_edit_create_reversible_revisions(self):
        hwp = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"template-v1"
        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.integrations.palette_registry.data_dir", return_value=Path(tmp)):
            first = palette_registry.install_hwp_template(
                hwp, "two-photo.hwp", label="수능합답2소사진5선지",
                slot_names=["문항번호", "문두", "사진1", "사진2", "발문", "ㄱ", "ㄴ", "ㄷ",
                            "1", "2", "3", "4", "5"],
                target_style="suneung",
            )
            session = palette_registry.start_edit_session(first["id"], 0)
            edit_file = Path(session["edit_file"])
            edit_file.write_bytes(hwp + b"-transparent-border")
            # Even if the browser passes a temporary/wrong activation slot,
            # an edit revision stays in its source palette family.
            second = palette_registry.save_edit_session(session["session_id"], "school")
            listed = palette_registry.list_palettes()

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(listed["packages"]), 2)
        self.assertEqual(listed["active"]["suneung"], second["id"])
        self.assertNotEqual(listed["active"].get("school"), second["id"])
        self.assertEqual(second["target_style"], "suneung")
        self.assertEqual(second["replaces"], first["id"])

    def test_template_editor_opens_only_existing_hwp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "copy.hwp"
            path.write_bytes(b"hwp")
            provider = HwpPaletteProvider(Path(tmp))
            with patch("app.integrations.hwppalette.os.startfile", create=True) as startfile:
                provider.open_template_editor(path)
        startfile.assert_called_once_with(str(path.resolve()))

    def test_palette_paint_test_uses_named_slots_and_registered_photo(self):
        markdown, warnings = _paint_test_markdown({
            "label": "수능합답1대사진5선지", "slot_count": 12,
            "slot_names": ["문항번호", "문두", "사진1", "발문", "ㄱ", "ㄴ", "ㄷ",
                           "1", "2", "3", "4", "5"],
        }, "시험그림")
        self.assertTrue(markdown.startswith("\\수능합답1대사진5선지\\\n1\n"))
        self.assertIn("\\시험그림\\", markdown)
        self.assertIn("<보기>에서 있는 대로", markdown)
        self.assertIn("ㄱ, ㄴ", markdown)
        self.assertEqual(warnings, [])

    def test_embedded_runtime_contains_school_and_csat_contracts(self):
        vendor = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
        self.assertTrue((vendor / "hwp_palette" / "cli.py").is_file())
        with tempfile.TemporaryDirectory() as tmp, \
             patch("app.integrations.hwppalette.data_dir", return_value=Path(tmp)):
            provider = HwpPaletteProvider(vendor)
            result = provider.validate_slot_contract({
                "학교합답0사진5선지": 11,
                "수능AI실제직접형": 9,
                "수능AI실제합답형": 11,
            })
            child_env = provider._child_env()
        self.assertTrue(result["ok"], result)
        self.assertIn("HWPPAL_DATA_DIR", child_env)
        self.assertIn(str(vendor), child_env["PYTHONPATH"])

    def test_question_preview_crops_unused_page_area(self):
        import fitz

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pdf = folder / "question.pdf"
            document = fitz.open()
            page = document.new_page(width=595, height=842)
            page.insert_text((52, 90), "1. compact question preview")
            document.save(pdf)
            document.close()
            full = HwpPaletteProvider._render_pdf(pdf, folder, crop_content=False)[0]
            cropped = HwpPaletteProvider._render_pdf(pdf, folder, crop_content=True)[0]
        self.assertLess(cropped["width"], full["width"])
        self.assertLess(cropped["height"], full["height"])

    def test_question_preview_excludes_suneung_page_header(self):
        import fitz

        document = fitz.open()
        page = document.new_page(width=841, height=1190)
        page.insert_text((98, 170), "1  TEST HEADER")
        page.insert_text((230, 220), "NAME  NUMBER")
        page.insert_text((99, 270), "1. current question only")
        page.insert_text((99, 340), "choice content")
        clip = HwpPaletteProvider._content_clip(page)
        document.close()

        self.assertGreater(clip.y0, 240)
        self.assertLess(clip.x1, 400)

        header_only = fitz.open()
        second_page = header_only.new_page(width=841, height=1190)
        second_page.insert_text((98, 170), "2  TEST HEADER")
        self.assertIsNone(HwpPaletteProvider._content_clip(second_page))
        header_only.close()

    def test_launcher_forces_utf8_output_for_detached_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hwp_palette").mkdir()
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            markdown = root / "question.md"
            markdown.write_text("test", encoding="utf-8")
            provider = HwpPaletteProvider(root)
            with patch("app.integrations.hwppalette.subprocess.Popen") as popen:
                popen.return_value.pid = 12345
                provider.launch(markdown)
        child_env = popen.call_args.kwargs["env"]
        self.assertEqual(child_env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(child_env["PYTHONUTF8"], "1")

    def test_launcher_reports_process_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hwp_palette").mkdir()
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            markdown = root / "question.md"
            markdown.write_text("test", encoding="utf-8")
            provider = HwpPaletteProvider(root)
            with patch("app.integrations.hwppalette.subprocess.Popen") as popen:
                popen.return_value.pid = 23456
                popen.return_value.poll.return_value = 0
                provider.launch(markdown)
                status = provider.process_status(23456)
        self.assertTrue(status["ok"])
        self.assertFalse(status["running"])

    def test_slot_contract_detects_match_and_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "library.json").write_text(json.dumps({
                "템플릿": [
                    {"name": "정답형", "label": "정답형", "slot_count": 8},
                    {"name": "합답형", "label": "합답형", "slot_count": 11},
                ]
            }, ensure_ascii=False), encoding="utf-8")
            provider = HwpPaletteProvider(root)
            result = provider.validate_slot_contract({"정답형": 8, "합답형": 12})
        self.assertFalse(result["ok"])
        self.assertTrue(result["templates"][1]["registered"])
        self.assertEqual(result["templates"][1]["actual"], 11)

    def test_current_active_contract_is_registered(self):
        status = integration_status()["hwppalette"]
        if not status["available"]:
            self.skipTest("hwpPalette sibling repository is not available")
        self.assertTrue(status["slot_contract"]["ok"], status["slot_contract"])

    def test_preview_is_hidden_isolated_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "palette"
            cache = Path(tmp) / "cache"
            (root / "hwp_palette").mkdir(parents=True)
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            provider = HwpPaletteProvider(root)

            calls = []

            class FakeProcess:
                returncode = 0
                pid = 123

                def __init__(self, args):
                    self.args = args

                def communicate(self, timeout=None):
                    Path(self.args[self.args.index("--output-hwp") + 1]).write_bytes(b"hwp")
                    Path(self.args[self.args.index("--output-pdf") + 1]).write_bytes(b"pdf")
                    return "", ""

            def fake_popen(args, **kwargs):
                calls.append(args)
                return FakeProcess(args)

            def fake_render(_pdf, folder, **_kwargs):
                (folder / "page-1.png").write_bytes(b"png")
                return [{"page_no": 1, "filename": "page-1.png", "width": 100, "height": 140}]

            with patch("app.integrations.hwppalette.data_dir", return_value=cache), \
                 patch("app.integrations.hwppalette.subprocess.Popen", side_effect=fake_popen), \
                 patch.object(provider, "_render_pdf", side_effect=fake_render):
                first = provider.render_preview("\\template\\\n1", scope="question")
                second = provider.render_preview("\\template\\\n1", scope="question")

        args = calls[0]
        self.assertIn("--hidden", args)
        self.assertIn("--output-hwp", args)
        self.assertIn("--output-pdf", args)
        self.assertIn("--hwp-pid-file", args)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["pages"][0]["image_url"],
                         f"/api/previews/{first['token']}/pages/1")

    def test_suneung_preview_uses_exam_pool_form_runner_and_separate_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "palette"
            cache = Path(tmp) / "cache"
            (root / "hwp_palette").mkdir(parents=True)
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            provider = HwpPaletteProvider(root)

            calls = []

            class FakeProcess:
                returncode = 0
                pid = 123

                def __init__(self, args):
                    self.args = args

                def communicate(self, timeout=None):
                    Path(self.args[self.args.index("--output-hwp") + 1]).write_bytes(b"hwp")
                    Path(self.args[self.args.index("--output-pdf") + 1]).write_bytes(b"pdf")
                    return "", ""

            def fake_popen(args, **kwargs):
                calls.append(args)
                return FakeProcess(args)

            def fake_render(_pdf, folder, **_kwargs):
                (folder / "page-1.png").write_bytes(b"png")
                return [{"page_no": 1, "filename": "page-1.png", "width": 100, "height": 140}]

            with patch("app.integrations.hwppalette.data_dir", return_value=cache), \
                 patch("app.integrations.hwppalette.subprocess.Popen", side_effect=fake_popen), \
                 patch.object(provider, "_render_pdf", side_effect=fake_render):
                school = provider.render_preview("\\template\\\n1", layout_style="school")
                suneung = provider.render_preview("\\template\\\n1", layout_style="suneung")
        args = calls[-1]
        self.assertIn("hwppalette_runner.py", str(args[1]))
        self.assertIn("suneung", args)
        self.assertNotEqual(school["token"], suneung["token"])
        self.assertEqual(suneung["layout_style"], "suneung")

    def test_preview_cache_changes_when_referenced_photo_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "palette"
            photo_dir = Path(tmp) / "photos"
            (root / "hwp_palette").mkdir(parents=True)
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            photo_dir.mkdir()
            image = photo_dir / "draft_7_01.png"
            image.write_bytes(b"old")
            provider = HwpPaletteProvider(root)
            with patch.object(provider, "photo_dirs", return_value=[photo_dir]):
                first = provider._preview_token(
                    "\\template\\\n\\draft_7_01\\", "question", True, "suneung"
                )
                image.write_bytes(b"new-image-content")
                second = provider._preview_token(
                    "\\template\\\n\\draft_7_01\\", "question", True, "suneung"
                )
        self.assertNotEqual(first, second)

    def test_conversion_photo_dirs_replace_historical_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "palette"
            old_dir = Path(tmp) / "job-old"
            current_dir = Path(tmp) / "job-current"
            (root / "hwp_palette").mkdir(parents=True)
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            (root / "data").mkdir()
            old_dir.mkdir()
            current_dir.mkdir()
            (old_dir / "shared.png").write_bytes(b"old")
            current = current_dir / "shared.png"
            current.write_bytes(b"current")
            (root / "data" / "config.json").write_text(json.dumps({
                "photo_dir": str(old_dir), "photo_dirs": [str(old_dir)],
            }), encoding="utf-8")
            provider = HwpPaletteProvider(root)
            with patch("app.integrations.hwppalette.subprocess.run") as run:
                run.return_value.returncode = 0
                provider.register_photo_dirs((current_dir,))

            resolved = provider.resolve_photo("shared")
            config = json.loads((root / "data" / "config.json").read_text(encoding="utf-8"))

        self.assertEqual(resolved, current.resolve())
        self.assertEqual(config["photo_dirs"], [str(current_dir.resolve())])

    def test_conversion_photo_lookup_does_not_fall_back_after_scope_is_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "palette"
            current_dir = Path(tmp) / "job-current"
            (root / "hwp_palette").mkdir(parents=True)
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            (root / "data").mkdir()
            current_dir.mkdir()
            (root / "data" / "config.json").write_text("{}", encoding="utf-8")
            provider = HwpPaletteProvider(root)
            provider.register_photo_dirs((current_dir,))

            with patch.object(
                provider,
                "photo_dirs",
                side_effect=AssertionError("historical fallback lookup was used"),
            ):
                resolved = provider.resolve_photo("missing")

        self.assertIsNone(resolved)

    def test_photo_resolution_rejects_ambiguous_fallback_same_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "palette"
            first_dir = Path(tmp) / "first"
            second_dir = Path(tmp) / "second"
            (root / "hwp_palette").mkdir(parents=True)
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            (root / "data").mkdir()
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "shared.png").write_bytes(b"first")
            (second_dir / "shared.png").write_bytes(b"second")
            (root / "data" / "config.json").write_text(json.dumps({
                "photo_dirs": [str(first_dir), str(second_dir)],
            }), encoding="utf-8")
            provider = HwpPaletteProvider(root)

            resolved = provider.resolve_photo("shared")

        self.assertIsNone(resolved)

    def test_preview_timeout_kills_recorded_hwp_and_runner(self):
        from app.integrations.hwppalette import _cleanup_timed_out_process

        class TimedOutProcess:
            pid = 123

            def __init__(self) -> None:
                self.killed = False
                self.wait_timeouts: list[float | None] = []

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout: float | None = None) -> int:
                self.wait_timeouts.append(timeout)
                return -1

        for is_windows in (True, False):
            with self.subTest(is_windows=is_windows), \
                 tempfile.TemporaryDirectory() as tmp, \
                 patch("app.integrations.hwppalette._terminate_process_id") as terminate:
                pid_path = Path(tmp) / "hwp.pid"
                pid_path.write_text("456", encoding="ascii")
                process = TimedOutProcess()

                _cleanup_timed_out_process(
                    process, pid_path, is_windows=is_windows,
                )

                terminated = [call.args[0] for call in terminate.call_args_list]
                self.assertEqual(terminated, {True: [456, 123], False: []}[is_windows])
                self.assertEqual(process.killed, not is_windows)
                self.assertEqual(process.wait_timeouts, [5])

    def test_failed_preview_kills_the_broken_hwp_process_before_retry(self):
        # Given: the isolated HWP process reports a COM server fault after recording its pid.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "palette"
            cache = Path(tmp) / "cache"
            (root / "hwp_palette").mkdir(parents=True)
            (root / "hwp_palette" / "cli.py").write_text("", encoding="utf-8")
            provider = HwpPaletteProvider(root)

            class FailedProcess:
                returncode = 1
                pid = 123

                def __init__(self, args):
                    self.args = args

                def communicate(self, timeout=None):
                    Path(self.args[self.args.index("--hwp-pid-file") + 1]).write_text(
                        "456", encoding="ascii",
                    )
                    return "", "pywintypes.com_error: (-2147417851, 'server exception')"

            with patch("app.integrations.hwppalette.data_dir", return_value=cache), \
                 patch("app.integrations.hwppalette.subprocess.Popen",
                       side_effect=lambda args, **_kwargs: FailedProcess(args)), \
                 patch("app.integrations.hwppalette._cleanup_timed_out_process") as cleanup:
                with self.assertRaises(HwpPaletteError):
                    provider.render_preview("\\template\\\n1", scope="set")

        cleanup.assert_called_once()

    def test_question_preview_accepts_unsaved_editor_payload(self):
        payload = QuestionIn(
            ask="빛의 반사에 대한 설명으로 옳은 것은?",
            layout_style="suneung",
            choices=[
                ChoiceIn(ord=1, text="입사각과 반사각은 같다.", is_answer=True),
                ChoiceIn(ord=2, text="반사각은 항상 0도이다."),
            ],
        )
        with patch("app.routes_integrations.hwppalette_provider.render_preview") as render:
            render.return_value = {"ok": True, "pages": []}
            result = preview_question(payload)
        self.assertTrue(result["ok"])
        markdown = render.call_args.args[0]
        self.assertIn("빛의 반사", markdown)
        self.assertIn("\\수능AI실제직접형\\", markdown)
        self.assertEqual(render.call_args.kwargs["scope"], "question")
        self.assertEqual(render.call_args.kwargs["layout_style"], "suneung")

    def test_reconstruction_preview_preserves_source_item_number(self):
        # Given: an isolated reconstruction carrying its source item number.
        payload = QuestionIn(
            ask="H는?",
            layout_style="suneung",
            style_meta={"reconstruction": {"enabled": True, "item_number": 20}},
            choices=[ChoiceIn(ord=1, text="보기", is_answer=True)],
        )

        # When: the real preview route builds HwpPalette markdown.
        with patch("app.routes_integrations.hwppalette_provider.render_preview") as render:
            render.return_value = {"ok": True, "pages": []}
            preview_question(payload)

        # Then: the isolated document keeps printed number 20.
        assert render.call_args.args[0].splitlines()[1] == "20"


class TestIntegrationRoutes(unittest.TestCase):
    MARK = "integration-route-test"

    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self._cleanup()
        conn = db.connect()
        try:
            self.sid = conn.execute(
                "INSERT INTO exam_set (name, short_code) VALUES (?, ?)",
                (self.MARK, "IRT"),
            ).lastrowid
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        conn = db.connect()
        try:
            conn.execute(
                "DELETE FROM set_item WHERE set_id IN "
                "(SELECT id FROM exam_set WHERE name=?)", (self.MARK,)
            )
            conn.execute("DELETE FROM exam_set WHERE name=?", (self.MARK,))
            conn.commit()
        finally:
            conn.close()

    def test_empty_set_is_not_launched(self):
        with self.assertRaises(HTTPException) as caught:
            typeset_set(self.sid)
        self.assertEqual(caught.exception.status_code, 409)

    def test_review_report_is_available_before_items_exist(self):
        report = review_report(self.sid)
        self.assertEqual(report["items"], [])
        self.assertEqual(report["issues"][0]["code"], "empty_set")

    def test_combo_question_counts_bogi_evidence(self):
        question = {
            "qtype": "합답형",
            "bogi_items": json.dumps([
                {"label": "ㄱ", "proposition_id": 1},
                {"label": "ㄴ", "custom_evidence": "직접 근거"},
                {"label": "ㄷ"},
            ], ensure_ascii=False),
        }
        linked, total = _evidence_summary(question, [
            {"combo": '["ㄱ"]'}, {"combo": '["ㄱ", "ㄴ"]'},
        ])
        self.assertEqual((linked, total), (2, 3))


if __name__ == "__main__":
    unittest.main()
