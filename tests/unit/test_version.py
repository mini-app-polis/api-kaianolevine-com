from __future__ import annotations

from kaianolevine_api.schemas import success_envelope
from kaianolevine_api.version import package_version


def test_envelope_reports_the_package_version() -> None:
    envelope = success_envelope({}, count=1, total=1)
    assert envelope.meta.version == package_version()
