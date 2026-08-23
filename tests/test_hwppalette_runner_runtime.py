from pathlib import Path
import sys

import olefile
import pytest


def test_runner_prefers_exam_pool_runtime_when_sibling_runtime_is_on_pythonpath(monkeypatch):
    # Given: development mode exposed a sibling HwpPalette before ExamPool's runtime.
    sibling = Path("C:/workspace/31_hwp_palette")
    monkeypatch.setattr(sys, "path", [str(sibling), *sys.path])
    from app.integrations import hwppalette_runner

    # When: the ExamPool-specific runner prepares its imports.
    embedded = hwppalette_runner._prefer_exam_pool_runtime()

    # Then: the patched runtime used by this runner wins import resolution.
    assert Path(sys.path[0]).resolve() == embedded
    assert embedded == (
        Path(hwppalette_runner.__file__).resolve().parents[2] / "vendor" / "hwp_typesetter"
    )


def test_subject_header_directive_is_stripped_and_maps_source_stems():
    from app.integrations import hwppalette_runner

    remainder, header = hwppalette_runner._extract_subject_header(
        "\\수능과목머리말\\\n화학Ⅰ\n\\수능합답1대사진5선지\\\n1\n"
    )
    assert header == "화학Ⅰ"
    assert remainder.startswith("\\수능합답1대사진5선지\\")
    assert hwppalette_runner.subject_header_from_source("c1_2026_11.pdf") == "화학Ⅰ"
    assert hwppalette_runner.subject_header_from_source("b2_2027_06") == "생명과학Ⅱ"
    assert hwppalette_runner.subject_header_from_source("p1_2024_11.pdf") == "물리Ⅰ"


def test_physics_form_header_is_replaced_with_source_subject(monkeypatch):
    from app.integrations import hwppalette_runner

    class FakeEngine:
        def __init__(self):
            self.calls = []

        def replace_all(self, find, repl):
            self.calls.append((find, repl))
            return True

    fake = FakeEngine()
    hwppalette_runner._apply_csat_subject_header(fake, "화학Ⅰ")
    assert ("(물리I)", "(화학Ⅰ)") in fake.calls
    assert ("(물리Ⅰ)", "(화학Ⅰ)") in fake.calls


def test_direct_large_photo_configures_measured_source_crop_frame():
    # Given: the active two-column layout used for item 20.
    from app.integrations import hwppalette_runner

    layout = {"column_width_mm": 93.99}

    # When: the direct large-photo source-crop contract is activated.
    hwppalette_runner._configure_source_crop_frame(layout)

    # Then: HWP sizing targets the measured 84.5% KICE frame width.
    assert layout["figure_frame_width_mm"] == 114.3
    assert layout["figure_target_ratio"] == 0.845


def test_reconstruction_passage_uses_kice_word_spacing_contract():
    # Given: the paragraph shape used by the source item-20 reconstruction.
    from app.integrations import hwppalette_runner

    # When: the source reconstruction contract is read.
    settings = hwppalette_runner._source_reconstruction_paragraph_settings()

    # Then: Korean words stay atomic while the seven-line source measure fits.
    assert settings == {
        "break_non_latin_word": 0,
        "condense_percent": 20,
        "character_ratio_percent": 85,
    }


def test_only_long_hapdap_fifth_choices_are_condensed():
    from app.integrations import hwppalette_runner

    long_block = "\n".join((
        "\\수능합답1대사진5선지\\", "234", "stem", "photo", "ask",
        "claim-a", "claim-b", "claim-c", "ㄱ", "ㄴ", "ㄱ, ㄴ", "ㄴ, ㄷ", "ㄱ, ㄴ, ㄷ",
    ))
    short_block = long_block.replace("ㄱ, ㄴ, ㄷ", "ㄱ, ㄷ")
    ordinary = long_block.replace("수능합답1대사진5선지", "수능정답1대사진5선지")
    small_hapdap = long_block.replace("수능합답1대사진5선지", "수능합답1소사진5선지")
    formula_combo = ordinary.replace(
        "ㄱ\nㄴ\nㄱ, ㄴ\nㄴ, ㄷ\nㄱ, ㄴ, ㄷ",
        "\\수식{A}\n\\수식{C}\n\\수식{A}, \\수식{B}\n"
        "\\수식{B}, \\수식{C}\n\\수식{A}, \\수식{B}, \\수식{C}",
    )
    ai_direct_formula_combo = formula_combo.replace(
        "수능정답1대사진5선지", "수능AI실제직접형",
    )
    radical_fractions = ordinary.replace(
        "ㄱ\nㄴ\nㄱ, ㄴ\nㄴ, ㄷ\nㄱ, ㄴ, ㄷ",
        "\\수식{\\frac{1}{3}} m/s\n"
        "\\수식{\\frac{\\sqrt{2}}{3}} m/s\n"
        "\\수식{\\frac{1}{2}} m/s\n"
        "\\수식{\\frac{2\\sqrt{2}}{3}} m/s\n1m/s",
    )

    assert hwppalette_runner._long_hapdap_fifth_choice_count(long_block) == 1
    assert hwppalette_runner._long_hapdap_fifth_choice_count(
        "\n".join((long_block, short_block, ordinary, long_block)),
    ) == 2
    assert hwppalette_runner._long_hapdap_fifth_choice_mask(
        "\n".join((ordinary, long_block, small_hapdap, ordinary, long_block)),
    ) == (False, True, True, False, True)
    assert hwppalette_runner._fifth_choice_ratio_mask(
        "\n".join((
            ordinary, long_block, formula_combo, ai_direct_formula_combo,
            small_hapdap, ordinary,
        )),
    ) == (False, True, True, True, True, False)
    assert hwppalette_runner._fifth_choice_ratio_mask(
        "\n".join((ordinary, radical_fractions, ordinary)),
    ) == (False, True, False)
    assert hwppalette_runner._LONG_HAPDAP_RATIO == 87


def test_ratio_mask_preserves_ordinals_across_every_registered_template_label():
    from app.integrations import hwppalette_runner

    direct = "\n".join((
        "\\수능정답1대사진5선지\\", "1", "stem", "photo", "ask",
        "a", "b", "c", "1", "2", "3", "4", "5",
    ))
    ai_hapdap = direct.replace(
        "수능정답1대사진5선지", "수능AI실제합답형",
    ).replace("1\n2\n3\n4\n5", "ㄱ\nㄷ\nㄱ, ㄴ\nㄴ, ㄷ\nㄱ, ㄴ, ㄷ")
    long_hapdap = ai_hapdap.replace(
        "수능AI실제합답형", "수능합답1대사진5선지",
    )
    split_experiment = long_hapdap.replace(
        "수능합답1대사진5선지", "수능AI실제실험형",
    )

    assert hwppalette_runner._fifth_choice_ratio_mask(
        "\n".join((direct, split_experiment, ai_hapdap, long_hapdap)),
    ) == (False, False, True)


def test_matching_hapdap_choice_ratio_changes_only_requested_paragraphs(monkeypatch):
    from app.integrations import hwppalette_runner

    hwppalette_runner._prefer_exam_pool_runtime()
    from hwp_palette.hwp import engine_library

    class FakeAction:
        def __init__(self):
            self.calls = []

        def Run(self, action):
            self.calls.append(action)

    class FakeHwp:
        def __init__(self):
            self.HAction = FakeAction()
            self.font_ratios = []
            self.paragraph_begins = 0

        def MoveParaBegin(self):
            self.paragraph_begins += 1

        def MoveSelParaEnd(self):
            pass

        def MoveParaEnd(self):
            pass

        def set_font(self, *, Ratio):
            self.font_ratios.append(Ratio)

    fake = FakeHwp()
    searches = []
    monkeypatch.setattr(engine_library, "_h", lambda: fake)
    monkeypatch.setattr(
        engine_library, "find_text",
        lambda text: searches.append(text) is None,
    )

    assert engine_library.set_matching_paragraph_character_ratio(
        "⑤ ㄱ, ㄴ, ㄷ", (False, True, False, True), character_ratio=87,
    )
    assert searches == ["⑤ ㄱ, ㄴ, ㄷ"] * 4
    assert fake.paragraph_begins == 2
    assert fake.font_ratios == [87, 87]


def test_ratio_mask_condenses_only_physically_wrapped_candidates(monkeypatch):
    from app.integrations import hwppalette_runner

    hwppalette_runner._prefer_exam_pool_runtime()
    from hwp_palette.hwp import engine_library

    class FakeAction:
        def __init__(self, owner):
            self.owner = owner

        def Run(self, action):
            if action == "MoveLineEnd":
                self.owner.pos = (0, self.owner.match, 5 if self.owner.wrapped[self.owner.match] else 10)
            elif action == "MoveParaEnd":
                self.owner.pos = (0, self.owner.match, 10)

    class FakeHwp:
        def __init__(self):
            self.match = -1
            self.wrapped = (False, True, False, True)
            self.pos = (0, 0, 0)
            self.font_ratios = []
            self.HAction = FakeAction(self)

        def MoveParaBegin(self):
            self.pos = (0, self.match, 0)

        def MoveSelParaEnd(self):
            pass

        def GetPos(self):
            return self.pos

        def SetPos(self, *pos):
            self.pos = pos

        def set_font(self, *, Ratio):
            self.font_ratios.append(Ratio)

    fake = FakeHwp()

    def find(_text):
        fake.match += 1
        return True

    monkeypatch.setattr(engine_library, "_h", lambda: fake)
    monkeypatch.setattr(engine_library, "find_text", find)

    assert engine_library.set_matching_paragraph_character_ratio(
        "⑤", (True, True, True, True), character_ratio=87, wrapped_only=True,
    )
    assert fake.font_ratios == [87, 87]


def test_ratio_mask_skips_non_choice_marker_occurrences(monkeypatch):
    from app.integrations import hwppalette_runner

    hwppalette_runner._prefer_exam_pool_runtime()
    from hwp_palette.hwp import engine_library, hwp_engine

    class FakeAction:
        def Run(self, _action):
            pass

    class FakeHwp:
        HAction = FakeAction()

        def __init__(self):
            self.match = -1
            self.font_ratios = []

        def MoveParaBegin(self):
            pass

        def MoveSelParaEnd(self):
            pass

        def MoveParaEnd(self):
            pass

        def set_font(self, *, Ratio):
            self.font_ratios.append(Ratio)

    fake = FakeHwp()
    paragraphs = ("① unrelated stem marker", "① ② ③ ④ ⑤ choices")

    def find(_text):
        fake.match += 1
        return fake.match < len(paragraphs)

    monkeypatch.setattr(engine_library, "_h", lambda: fake)
    monkeypatch.setattr(engine_library, "find_text", find)
    monkeypatch.setattr(
        hwp_engine, "read_selection_direct", lambda: paragraphs[fake.match],
    )

    assert engine_library.set_matching_paragraph_character_ratio(
        "①", (True,), character_ratio=87,
        required_paragraph_text=("①", "②", "③", "④", "⑤"),
    )
    assert fake.font_ratios == [87]


def test_direct_reconstruction_wraps_stem_and_atomic_size_phrase():
    # Given: item 20 has separate stem and question paragraphs.
    from app.integrations import hwppalette_runner

    source = Path(hwppalette_runner.__file__).read_text(encoding="utf-8")

    # Then: both paragraphs receive measured Korean wrap contracts; the prompt
    # keeps `크기,` at the source line endpoint rather than pulling in `공기`.
    assert 'set_paragraph_word_boundary_wrap("가만히 놓았더니")' in source
    assert ('set_paragraph_word_boundary_wrap(\n'
            '                    "물체의 크기,", character_ratio=90,\n'
            '            )') in source


def test_derived_figure_templates_are_parseable_and_match_slot_contracts(tmp_path, monkeypatch):
    # Given: the active CSAT palette lacks native direct one-photo fragments.
    from app.integrations import palette_registry

    runtime = tmp_path / "runtime"
    seed = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter" / "seed_data"
    data_root = tmp_path / "data"
    monkeypatch.setattr(palette_registry, "data_dir", lambda: data_root)
    digest = "test-active-suneung"
    package = data_root / "typesetting_palettes" / "packages" / digest
    (package / "fragments").mkdir(parents=True)
    (package / "normalized.json").write_text(
        '{"digest":"test-active-suneung","items":[]}', encoding="utf-8"
    )
    palette_registry._save_registry({
        "schema_version": palette_registry.REGISTRY_SCHEMA,
        "active": {"suneung": digest},
        "packages": [{"digest": digest, "items": []}],
    })

    # When: ExamPool materializes the active palette for HwpPalette.
    palette_registry.materialize_active(runtime, seed, force=True)

    # Then: real large/small direct fragments with no bogi slots are registered.
    expected = {
        "수능정답1대사진5선지": ["문항번호", "문두", "사진1", "발문", "1", "2", "3", "4", "5"],
        "수능정답1소사진5선지": ["문항번호", "문두", "사진1", "발문", "1", "2", "3", "4", "5"],
        "수능정답2소사진무캡션5선지": [
            "문항번호", "문두", "사진1", "사진2", "발문", "1", "2", "3", "4", "5",
        ],
        "수능정답2대사진5선지": [
            "문항번호", "문두", "사진1", "사진2", "발문", "1", "2", "3", "4", "5",
        ],
        "수능정답상하사진5선지": [
            "문항번호", "문두", "사진1", "자료", "발문", "1", "2", "3", "4", "5",
        ],
        "수능정답1대사진그림5선지": [
            "문항번호", "문두", "사진1", "발문",
            "선지사진1", "선지사진2", "선지사진3", "선지사진4", "선지사진5",
        ],
        "수능합답1대사진5선지": [
            "문항번호", "문두", "사진1", "발문",
            "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
        ],
        "수능합답1소사진5선지": [
            "문항번호", "문두", "사진1", "발문",
            "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
        ],
        "수능합답2소사진무캡션5선지": [
            "문항번호", "문두", "사진1", "사진2", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
        ],
        "수능합답2대사진5선지": [
            "문항번호", "문두", "사진1", "사진2", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
        ],
        "수능합답상하사진5선지": [
            "문항번호", "문두", "사진1", "자료", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
        ],
    }
    def assert_parseable_hwp(fragment: Path) -> None:
        with olefile.OleFileIO(fragment) as hwp:
            streams = {"/".join(parts) for parts in hwp.listdir()}
            assert {"FileHeader", "DocInfo", "BodyText/Section0"} <= streams
            assert hwp.openstream("FileHeader").read(17) == b"HWP Document File"

    parsed_fragments = []
    for label, slot_names in expected.items():
        spec = palette_registry.active_template("suneung", label)
        assert spec is not None
        assert spec["slot_names"] == slot_names
        fragment = runtime / "fragments" / f"exampool_{spec['file']}"
        assert_parseable_hwp(fragment)
        parsed_fragments.append(fragment)

    # Mutation proof: a truncated file can still be non-empty (and could satisfy
    # an arbitrary size threshold), but a real OLE/HWP open must reject it.
    corrupted = tmp_path / "truncated.hwp"
    corrupted.write_bytes(parsed_fragments[0].read_bytes()[:4096])
    with pytest.raises(AssertionError):
        assert_parseable_hwp(corrupted)


def test_template_marker_search_continues_from_previous_fragment(monkeypatch):
    # Given: a large conversion set whose earlier fragments already occupy the document.
    from app.integrations import hwppalette_runner

    hwppalette_runner._prefer_exam_pool_runtime()
    from hwp_palette.hwp import engine_library

    class FakeHwp:
        def __init__(self):
            self.doc_begin_calls = 0
            self.positions = []

        def MoveDocBegin(self):
            self.doc_begin_calls += 1

        def SetPos(self, *position):
            self.positions.append(position)

    fake = FakeHwp()
    monkeypatch.setattr(engine_library, "_h", lambda: fake)
    monkeypatch.setattr(engine_library, "find_text", lambda marker: marker == "next")

    # When: the first marker and a subsequent marker are located.
    assert engine_library._find_template_marker("next", None)
    assert engine_library._find_template_marker("next", (0, 12, 0))

    # Then: only the first lookup scans from the document beginning; subsequent
    # lookups start at the preceding fragment instead of rescanning the whole set.
    assert fake.doc_begin_calls == 1
    assert fake.positions == [(0, 12, 0)]


def test_trailing_page_probe_uses_zero_based_page_text_index(monkeypatch):
    # Given: HWP exposes a two-page document while GetPageText is zero-based.
    from app.integrations import hwppalette_runner

    hwppalette_runner._prefer_exam_pool_runtime()
    from hwp_palette.hwp import engine_library

    class FakeHwp:
        PageCount = 2

        def __init__(self):
            self.text_pages = []
            self.goto_pages = []

        def get_page_text(self, page):
            self.text_pages.append(page)
            return "제4 교시"

        def goto_page(self, page):
            self.goto_pages.append(page)

        def DeletePage(self):
            return True

    fake = FakeHwp()
    monkeypatch.setattr(engine_library, "_h", lambda: fake)

    # When: the trailing form page is inspected and removed.
    assert engine_library.delete_trailing_csat_form_page()

    # Then: text inspection uses the final zero-based index while navigation
    # keeps pyhwpx's public one-based page number.
    assert fake.text_pages == [1]
    assert fake.goto_pages == [2]
