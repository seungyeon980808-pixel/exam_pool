from pathlib import Path
from unittest.mock import MagicMock, patch

from app.authoring.figures import FiveELocalProvider


def test_fivee_uses_a_fallback_port_when_legacy_server_occupies_preferred_port() -> None:
    # Given: a listener occupies the configured port but fails the 5E health contract.
    provider = FiveELocalProvider(root=Path("C:/fivee"), port=18190)
    process = MagicMock()
    process.poll.return_value = None

    # When: ExamPool ensures its verified 5E server is available.
    with patch.object(provider, "_check_installation"), \
         patch.object(provider, "_server_is_ready", side_effect=(False, True)), \
         patch.object(provider, "_port_is_open", return_value=True), \
         patch.object(FiveELocalProvider, "_server_process", None), \
         patch("app.authoring.figures.subprocess.Popen", return_value=process) as popen:
        provider._ensure_server()

    # Then: the legacy listener is preserved and ExamPool starts on another port.
    assert provider.port != 18190
    assert popen.call_args.args[0][2] == str(provider.port)
