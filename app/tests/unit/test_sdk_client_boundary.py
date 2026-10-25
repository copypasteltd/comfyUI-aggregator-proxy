"""The optional client rehearsal must never send its synthetic Key elsewhere."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from native_openai_sdk import loopback_origin  # noqa: E402


@pytest.mark.parametrize("origin", [
    "https://api.openai.com", "http://example.invalid:8080", "http://localhost:8080",
    "http://127.0.0.1", "https://127.0.0.1:8080", "http://127.0.0.1:8080/v1",
    "http://secret@127.0.0.1:8080", "http://127.0.0.1:8080?target=external",
    "http://127.0.0.1:8080#external", "http://127.0.0.1.example.invalid:8080",
    "http://2130706433:8080", "http://[::1]:8080",
])
def test_sdk_rehearsal_rejects_external_ambiguous_or_credentialed_targets(origin: str) -> None:
    with pytest.raises(RuntimeError, match="sdk_owned_loopback_origin_required"):
        loopback_origin(origin)


def test_sdk_rehearsal_uses_the_owned_explicit_loopback_port() -> None:
    assert loopback_origin("http://127.0.0.1:41873/") == "http://127.0.0.1:41873"
