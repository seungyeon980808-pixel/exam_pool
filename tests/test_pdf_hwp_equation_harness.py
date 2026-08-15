from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.generate_equation_hwp_proof import PROFILES as GENERATION_PROFILES
from tools.verify_equation_hwp_proof import PROFILES as VERIFICATION_PROFILES


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tools" / "corpus_pdf_hwp_equation_harness.py"


def test_harness_inventory_help_exposes_all_three_commands() -> None:
    completed = subprocess.run(
        [sys.executable, str(HARNESS), "--help"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
    )

    assert "inventory" in completed.stdout
    assert "run-residual" in completed.stdout
    assert "verify-mapping" in completed.stdout


def test_fresh_baseline_contract_is_exactly_52_pdfs_1040_items_163_residuals() -> None:
    code = (
        "import json,sys;sys.path[:0]=[r'" + str(ROOT / "tools") + "',r'" + str(ROOT) + "'];"
        "from pdf_hwp_equation_corpus import load_fresh_baseline,subject_counts;"
        "b=load_fresh_baseline();print(json.dumps({'papers':len(b.papers),'items':b.detected_count,"
        "'residuals':len(b.residuals),'subjects':subject_counts(b.residuals)}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload == {
        "papers": 52, "items": 1040, "residuals": 163,
        "subjects": {"p1": 16, "c1": 66, "c2": 66, "b1": 3, "b2": 0, "e1": 4, "e2": 8},
    }


def test_safe_main_proof_contract_uses_source_geometry_for_q19() -> None:
    selected_cases = GENERATION_PROFILES["safe-main"][1]
    q19_formula = next(
        formula for paper, item_number, formula in selected_cases
        if paper == "p1_2025_09" and item_number == 19
    )
    required_script = VERIFICATION_PROFILES["safe-main"][1]["root_and_subscript"]
    fraction_script = VERIFICATION_PROFILES["safe-main"][1]["fraction_superscript"]

    assert q19_formula == r"\sqrt{5}v_{0}"
    assert required_script == "sqrt {5}v_{0}"
    assert fraction_script == "{3v_{0}^{2}} over {4g}"


def test_safe_root_proof_contract_uses_the_measured_bar_extent() -> None:
    selected_cases = GENERATION_PROFILES["safe-root"][1]
    assert selected_cases == (("p1_2026_11", 15, r"I=4\sqrt{2}I_{0}"),)
    assert VERIFICATION_PROFILES["safe-root"][1]["root_and_subscript"] == (
        "I=4sqrt {2}I_{0}"
    )
