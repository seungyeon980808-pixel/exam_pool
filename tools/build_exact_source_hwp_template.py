"""Build the one-slot template used to preserve an entire source question image."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from pyhwpx import Hwp


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "hwp_templates" / "csat_direct_one_large.hwp"
TARGET = ROOT / "assets" / "hwp_templates" / "csat_exact_source_one_large.hwp"
SECTION = "Contents/section0.xml"


def _local(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _convert(source: Path, target: Path, format_name: str) -> None:
    hwp = Hwp(visible=False, register_module=True, on_quit=False)
    try:
        if not hwp.open(str(source.resolve())):
            raise RuntimeError(f"cannot open template: {source}")
        if not hwp.save_as(str(target.resolve()), format=format_name):
            raise RuntimeError(f"cannot save template: {target}")
    finally:
        hwp.quit()


def build(source: Path = SOURCE, target: Path = TARGET) -> Path:
    """Retain the large picture slot while removing duplicate text and choices."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exampool-exact-template-") as folder:
        work = Path(folder)
        expanded = work / "source.hwpx"
        rewritten = work / "exact.hwpx"
        _convert(source, expanded, "HWPX")
        with zipfile.ZipFile(expanded) as archive:
            section = ET.fromstring(archive.read(SECTION))
            paragraphs = list(section)
            if len(paragraphs) < 2:
                raise RuntimeError("large-picture template structure is unavailable")
            # Paragraph zero owns the section properties.  It must remain, but
            # its question-number/prompt text would duplicate the source crop.
            for run in [node for node in paragraphs[0] if _local(node) == "run"]:
                if not any(_local(node) == "secPr" for node in run.iter()):
                    paragraphs[0].remove(run)
                else:
                    for node in run.iter():
                        if _local(node) == "t":
                            node.text = ""
                            for child in list(node):
                                child.tail = ""
            # Paragraph one is the original full-width ``사진1`` slot.
            for paragraph in paragraphs[2:]:
                section.remove(paragraph)
            rewritten_section = ET.tostring(section, encoding="utf-8", xml_declaration=True)
            with zipfile.ZipFile(rewritten, "w") as output:
                for info in archive.infolist():
                    payload = rewritten_section if info.filename == SECTION else archive.read(info)
                    output.writestr(copy.copy(info), payload)
        _convert(rewritten, target, "HWP")
    return target


if __name__ == "__main__":
    print(build())
