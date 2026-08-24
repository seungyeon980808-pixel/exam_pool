"""중앙 설정. 환경변수 기반, 하드코딩 제거.

모든 경로·포트·서드파티 설정은 이 모듈을 통해 읽는다.
"""
import logging
import os
import sys
from pathlib import Path

# ── 로깅 ───────────────────────────────────────────
_log_level = os.environ.get("EXAMPOOL_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("exampool")

# ── 포트 ───────────────────────────────────────────
HTTP_PORT = int(os.environ.get("EXAMPOOL_PORT", "8632"))
FIVE_E_HTTP_PORT = int(os.environ.get("EXAMPOOL_FIVEE_PORT", "8190"))
FIVE_E_BRIDGE_PORT_START = int(os.environ.get("EXAMPOOL_FIVEE_BRIDGE_PORT", "8579"))

# ── 외부 프로젝트 루트 ─────────────────────────────
FIVE_E_ROOT = Path(os.environ.get(
    "EXAMPOOL_5E_ROOT",
    str(Path(__file__).resolve().parent.parent.parent / "51_5E" / "5E_main"),
))
HWPPAL_ROOT = Path(os.environ.get(
    "EXAMPOOL_HWPPAL_ROOT",
    str(Path(__file__).resolve().parent.parent.parent / "hwppalette"),
))
PHOTO_ROOT = os.environ.get("EXAMPOOL_PHOTO_ROOT", "")

# ── Codex App Server ───────────────────────────────
CODEX_COMMAND = os.environ.get("EXAMPOOL_CODEX_COMMAND", "codex")
CODEX_MODEL = os.environ.get("EXAMPOOL_CODEX_MODEL", "")
CODEX_REASONING_EFFORT = os.environ.get("EXAMPOOL_CODEX_REASONING", "")
CODEX_TIMEOUT = int(os.environ.get("EXAMPOOL_CODEX_TIMEOUT", "300"))

# ── 계산된 경로 ────────────────────────────────────
FIVE_E_MCP_SERVER = FIVE_E_ROOT / "tools" / "mcp-5e" / "server.js"
FIVE_E_SAMPLES = FIVE_E_ROOT / "tools" / "mcp-5e" / "samples"
