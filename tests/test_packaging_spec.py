from __future__ import annotations

import ast
import re
from pathlib import Path


SPEC_PATH = Path(__file__).resolve().parent.parent / "packaging" / "mailbox-rescue.spec"


def _load_icu_filter():
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"))
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_is_unintended_windows_icu_binary"
        ),
        None,
    )
    assert function is not None, "packaging spec must define the Windows ICU filter"

    namespace = {"Path": Path, "re": re}
    exec(compile(ast.Module(body=[function], type_ignores=[]), SPEC_PATH, "exec"), namespace)
    return namespace["_is_unintended_windows_icu_binary"]


def test_packaging_spec_rejects_external_icu_runtime_collision() -> None:
    is_unintended_icu = _load_icu_filter()

    assert is_unintended_icu("icuuc.dll") is True
    assert is_unintended_icu("icudt78.dll") is True
    assert is_unintended_icu(r"nested\ICUDT99.DLL") is True
    assert is_unintended_icu("PySide6/Qt6Core.dll") is False
    assert is_unintended_icu("icu.dll") is False
