# -*- coding: utf-8 -*-
r"""HwpPalette 본체.

층 규칙 (2026-07-28 폴더 개편) — **화살표는 아래로만 간다**:

    core    기반. 위 어느 것도 부르지 않는다
              appinfo applog paths settings backup clipboard screens hotkey
      ↓
    design  생김새 부품. core 만 부른다
              theme ui_fx roundbtn popover dialogs disclosure
    model   데이터·규칙. 한글도 화면도 모른다
              palette library chip parser form_fill
              builtin_actions builtin_chars func_catalog
      ↓
    hwp     한글 COM. model 까지만 부른다
              hwp_engine engine_library exam_engine preview form_markdown
              hwp_dock (창을 끌어온다)
      ↓
    ui      화면. 위를 다 부른다
              palette_ui store_ui library_ui form_fill_ui form_table_ui
              help_ui help_content onboarding tutorial tutorials dock_bar
      ↓
    app.py  창 조립 (루트의 main.py 가 이것만 부른다)

이 경계를 만든 이유: 코드는 이미 이 순서로 얽혀 있었는데(의존 그래프 실측
12층, 순환 1건) **폴더가 없어서 눈에 안 보였다.** 폴더로 만들어 두면 규칙을
어기는 임포트가 눈에 띈다 — model/ 안에서 `from hwp_palette.hwp import ...`
가 보이면 그건 잘못된 것이다.
"""
