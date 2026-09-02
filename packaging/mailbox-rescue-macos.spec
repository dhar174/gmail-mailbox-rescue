# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import tomllib

repo_root = Path(SPECPATH).resolve().parent
with (repo_root / "pyproject.toml").open("rb") as project_file:
    version = tomllib.load(project_file)["project"]["version"]

a = Analysis(
    [str(repo_root / "packaging" / "run_app.py")],
    pathex=[str(repo_root / "src")],
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
    excludes=["tkinter", "unittest", "pdb", "pytest", "pytest_cov", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mailbox Rescue",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    # Qt handles its own events; argv emulation can interfere with its event loop.
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Mailbox Rescue")
app = BUNDLE(
    coll,
    name="Mailbox Rescue.app",
    bundle_identifier="com.dhar174.mailbox-rescue",
    version=version,
    info_plist={
        "CFBundleShortVersionString": version,
        "NSHighResolutionCapable": True,
    },
)
