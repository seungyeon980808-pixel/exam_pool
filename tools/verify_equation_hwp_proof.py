"""Verify representative formulas are editable EQEDIT records in the proof HWP."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import zlib

import olefile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_BASE = PROJECT_ROOT / ".omo/evidence/pdf-hwp-generalization/equation-glyphs"
EQEDIT_TAG = 88
PROFILES = {
    "initial": ("actual-hwp", {
        "three_fractions": "{1} over {lambda_{a}}-{1} over {lambda_{b}}={1} over {lambda_{c}}",
        "root_and_subscript": "I=4sqrt {2}I_{0}",
        "subscripts_in_fraction": "h{y_{0}} over {v_{0}}",
        "overbar": "bar{S_{1}S_{2}}",
        "superscript_in_denominator": "k={12mgh} over {d^{2}}",
    }),
    "safe-main": ("actual-hwp-safe-main", {
        "three_fractions": "{1} over {lambda_{a}}-{1} over {lambda_{b}}={1} over {lambda_{c}}",
        "root_and_subscript": "sqrt {5}v_{0}",
        "fraction_superscript": "{3v_{0}^{2}} over {4g}",
        "subscripts_in_fraction": "h{y_{0}} over {v_{0}}",
    }),
    "safe-overbar": ("actual-hwp-safe-overbar", {"overbar": "bar{PQ}"}),
    "safe-root": ("actual-hwp-safe-root", {
        "root_and_subscript": "I=4sqrt {2}I_{0}",
    }),
}


class EquationProofVerificationError(RuntimeError):
    """Raised when HWP equation records are truncated or malformed."""


def _records(data: bytes):
    offset = 0
    while offset + 4 <= len(data):
        header = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        tag = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        payload = data[offset:offset + size]
        if len(payload) != size:
            raise EquationProofVerificationError("truncated HWP record payload")
        yield tag, level, payload
        offset += size
    if offset != len(data):
        raise EquationProofVerificationError("trailing bytes after HWP records")


def _equation_script(payload: bytes) -> str:
    if len(payload) < 6:
        raise EquationProofVerificationError("short EQEDIT record")
    length = struct.unpack_from("<H", payload, 4)[0]
    end = 6 + length * 2
    if end > len(payload):
        raise EquationProofVerificationError("truncated EQEDIT script")
    return payload[6:end].decode("utf-16le")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=tuple(PROFILES), default="initial")
    args = parser.parse_args()
    directory, required = PROFILES[args.profile]
    proof_root = EVIDENCE_BASE / directory
    hwp_path = proof_root / "output/converted.hwp"
    output_path = proof_root / "structure/equation-scripts.json"
    with olefile.OleFileIO(hwp_path) as document:
        header = document.openstream("FileHeader").read()
        section = document.openstream("BodyText/Section0").read()
    if struct.unpack_from("<I", header, 36)[0] & 1:
        section = zlib.decompress(section, -15)
    scripts = [
        _equation_script(payload)
        for tag, _, payload in _records(section)
        if tag == EQEDIT_TAG
    ]
    matched = {
        name: script in scripts for name, script in required.items()
    }
    payload = {
        "schema_version": 1, "hwp_path": str(hwp_path.resolve()),
        "eqedit_tag": EQEDIT_TAG, "eqedit_count": len(scripts),
        "required_scripts": required, "matched": matched,
        "raw_pua_in_scripts": sorted({
            f"U+{ord(char):04X}" for script in scripts for char in script
            if 0xE000 <= ord(char) <= 0xF8FF
        }),
        "scripts": scripts,
        "passed": all(matched.values()) and not any(
            0xE000 <= ord(char) <= 0xF8FF for script in scripts for char in script
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output_path.resolve()), "eqedit_count": len(scripts),
        "matched": matched, "passed": payload["passed"],
    }, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
