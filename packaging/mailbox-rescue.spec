# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import os
import re
from pathlib import Path

block_cipher = None

# Base directory is the repository root
spec_dir = Path(SPECPATH).resolve()
repo_root = spec_dir.parent if spec_dir.name == "packaging" else spec_dir
src_dir = repo_root / "src"
run_script = repo_root / "packaging" / "run_app.py"
version_info_path = repo_root / "packaging" / "version_info.txt"


def _is_unintended_windows_icu_binary(binary_name: str) -> bool:
    """Reject external ICU4C DLLs that shadow the Windows system ICU API."""
    filename = Path(binary_name.replace("\\", "/")).name.lower()
    return filename == "icuuc.dll" or re.fullmatch(r"icudt\d+\.dll", filename) is not None

a = Analysis(
    [str(run_script)],
    pathex=[str(src_dir)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "mailbox_rescue",
        "mailbox_rescue.app",
        "mailbox_rescue.config",
        "mailbox_rescue.auth.google_oauth",
        "mailbox_rescue.gmail.client",
        "mailbox_rescue.export.service",
        "mailbox_rescue.export.eml",
        "mailbox_rescue.export.mbox",
        "mailbox_rescue.export.manifest",
        "mailbox_rescue.export.metadata",
        "mailbox_rescue.export.models",
        "mailbox_rescue.export.report",
        "mailbox_rescue.export.retry",
        "mailbox_rescue.export.verify",
        "mailbox_rescue.storage.checkpoint",
        "mailbox_rescue.ui.main_window",
        "mailbox_rescue.ui.worker",
        "googleapiclient",
        "googleapiclient.discovery",
        "google.auth",
        "google.oauth2.credentials",
        "google_auth_oauthlib.flow",
        "platformdirs",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pdb",
        "pytest",
        "pytest_cov",
        "ruff",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Qt 6 uses Windows' System32 ICU API. If an unrelated ICU4C distribution is on
# PATH during analysis, PyInstaller can collect its incompatible generic
# icuuc.dll plus versioned data DLL and cause QtCore to fail with WinError 127.
a.binaries = [
    binary for binary in a.binaries if not _is_unintended_windows_icu_binary(binary[0])
]

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mailbox Rescue",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(version_info_path) if version_info_path.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Mailbox Rescue",
)
