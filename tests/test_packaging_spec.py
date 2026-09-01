from __future__ import annotations

import runpy
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent.parent / "packaging" / "mailbox-rescue.spec"


class _FakeAnalysis:
    def __init__(self, *_args, **_kwargs) -> None:
        self.binaries = [
            ("icuuc.dll", "external/icuuc.dll", "BINARY"),
            ("icudt78.dll", "external/icudt78.dll", "BINARY"),
            (r"nested\ICUDT99.DLL", "external/icudt99.dll", "BINARY"),
            ("PySide6/Qt6Core.dll", "pyside/Qt6Core.dll", "BINARY"),
            ("icu.dll", "windows/icu.dll", "BINARY"),
        ]
        self.pure = []
        self.zipped_data = []
        self.scripts = []
        self.zipfiles = []
        self.datas = []


def _fake_artifact(*_args, **_kwargs):
    return object()


def test_packaging_spec_rejects_external_icu_runtime_collision() -> None:
    result = runpy.run_path(
        str(SPEC_PATH),
        init_globals={
            "SPECPATH": str(SPEC_PATH.parent),
            "Analysis": _FakeAnalysis,
            "PYZ": _fake_artifact,
            "EXE": _fake_artifact,
            "COLLECT": _fake_artifact,
        },
    )

    assert [binary[0] for binary in result["a"].binaries] == [
        "PySide6/Qt6Core.dll",
        "icu.dll",
    ]
