"""Regression and integration tests for safe portable package updates."""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from build_portable import build_portable_package


def test_portable_archive_contains_only_fresh_install_payload(tmp_path):
    project = tmp_path / "project"
    dist = tmp_path / "dist" / "ChestScanner"
    (dist / "_internal").mkdir(parents=True)
    (project / "assets" / "digits").mkdir(parents=True)
    (project / "assets" / "custom").mkdir()
    (dist / "ChestScanner.exe").write_bytes(b"exe")
    (dist / "_internal" / "runtime.dll").write_bytes(b"dll")
    (dist / "config.json").write_text("private config", encoding="utf-8")
    (dist / "profiles.json").write_text("private profiles", encoding="utf-8")
    (dist / "profiles").mkdir()
    (dist / "profiles" / "private.json").write_text("private", encoding="utf-8")
    (project / "assets" / "digits" / "1.png").write_bytes(b"digit")
    (project / "assets" / "custom" / "private.png").write_bytes(b"private image")
    (project / "LICENSE").write_text("license", encoding="utf-8")
    (project / "update_portable.bat").write_text("updater", encoding="utf-8")
    (project / "update_portable.ps1").write_text("powershell updater", encoding="utf-8")
    output = tmp_path / "release" / "ChestScanner-portable.zip"

    build_portable_package(dist, project, output)

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "ChestScanner/ChestScanner.exe" in names
        assert "ChestScanner/_internal/runtime.dll" in names
        assert "ChestScanner/assets/digits/1.png" in names
        assert "ChestScanner/items.json" not in names
        assert "ChestScanner/LICENSE" in names
        assert "update_portable.bat" in names
        assert "update_portable.ps1" in names
        assert not any("config.json" in name for name in names)
        assert not any("profiles" in name.lower() for name in names)
        assert not any("private" in name.lower() for name in names)
        assert not any(name.startswith("ChestScanner/results/") for name in names)


def _run_updater(script: Path, source: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-Source", str(source), "-Target", str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
    )


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell required")
def test_portable_updater_preserves_user_data_on_success(tmp_path):
    package = tmp_path / "package"
    source = package / "ChestScanner"
    (source / "_internal").mkdir(parents=True)
    (source / "assets" / "digits").mkdir(parents=True)
    (source / "assets" / "digits" / "new.png").write_bytes(b"new asset")
    (source / "_internal" / "new.dll").write_bytes(b"new runtime")
    (source / "ChestScanner.exe").write_bytes(b"NEW-EXE")
    (source / "LICENSE").write_text("new license", encoding="utf-8")
    shutil.copy2("update_portable.ps1", package / "update_portable.ps1")

    target = tmp_path / "existing"
    (target / "_internal").mkdir(parents=True)
    (target / "assets" / "custom").mkdir(parents=True)
    (target / "assets" / "old.png").write_bytes(b"old asset")
    (target / "assets" / "custom" / "mine.png").write_bytes(b"user image")
    (target / "_internal" / "old.dll").write_bytes(b"old runtime")
    (target / "ChestScanner.exe").write_bytes(b"OLD-EXE")
    (target / "items.json").write_text("user items", encoding="utf-8")
    (target / "config.json").write_text("user config", encoding="utf-8")
    (target / "profiles.json").write_text("user profiles", encoding="utf-8")
    (target / "profiles").mkdir()
    (target / "profiles" / "mine.json").write_text("profile", encoding="utf-8")
    (target / "results").mkdir()
    (target / "results" / "history.txt").write_text("history", encoding="utf-8")

    result = _run_updater(package / "update_portable.ps1", source, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "ChestScanner.exe").read_bytes() == b"NEW-EXE"
    assert not (target / "_internal" / "old.dll").exists()
    assert (target / "_internal" / "new.dll").read_bytes() == b"new runtime"
    assert not (target / "assets" / "old.png").exists()
    assert (target / "assets" / "digits" / "new.png").read_bytes() == b"new asset"
    assert (target / "assets" / "custom" / "mine.png").read_bytes() == b"user image"
    assert (target / "items.json").read_text(encoding="utf-8") == "user items"
    assert (target / "config.json").read_text(encoding="utf-8") == "user config"
    assert (target / "profiles.json").read_text(encoding="utf-8") == "user profiles"
    assert (target / "profiles" / "mine.json").read_text(encoding="utf-8") == "profile"
    assert (target / "results" / "history.txt").read_text(encoding="utf-8") == "history"


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell required")
def test_portable_updater_rejects_broken_package_before_touching_target(tmp_path):
    package = tmp_path / "broken-package"
    source = package / "ChestScanner"
    source.mkdir(parents=True)
    (source / "ChestScanner.exe").write_bytes(b"new exe")
    (source / "_internal").mkdir()
    # Missing assets and LICENSE: preflight must abort before backing up/copying.
    shutil.copy2("update_portable.ps1", package / "update_portable.ps1")

    target = tmp_path / "existing"
    target.mkdir()
    (target / "ChestScanner.exe").write_bytes(b"old exe")
    (target / "_internal").mkdir()
    (target / "_internal" / "old.dll").write_bytes(b"old runtime")
    (target / "items.json").write_text("user items", encoding="utf-8")

    result = _run_updater(package / "update_portable.ps1", source, target)
    assert result.returncode != 0
    assert (target / "ChestScanner.exe").read_bytes() == b"old exe"
    assert (target / "_internal" / "old.dll").read_bytes() == b"old runtime"
    assert (target / "items.json").read_text(encoding="utf-8") == "user items"


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell required")
def test_portable_updater_rolls_back_program_files_after_copy_error(tmp_path):
    package = tmp_path / "package"
    source = package / "ChestScanner"
    (source / "_internal").mkdir(parents=True)
    (source / "assets").mkdir()
    (source / "_internal" / "a-new.dll").write_bytes(b"new runtime file")
    denied_file = source / "_internal" / "z-denied.dll"
    denied_file.write_bytes(b"unreadable source")
    (source / "ChestScanner.exe").write_bytes(b"NEW-EXE")
    (source / "LICENSE").write_text("new license", encoding="utf-8")
    shutil.copy2("update_portable.ps1", package / "update_portable.ps1")

    target = tmp_path / "existing"
    (target / "_internal").mkdir(parents=True)
    (target / "_internal" / "old.dll").write_bytes(b"old runtime file")
    (target / "assets").mkdir()
    (target / "assets" / "old.png").write_bytes(b"old asset")
    (target / "ChestScanner.exe").write_bytes(b"OLD-EXE")
    (target / "LICENSE").write_text("old license", encoding="utf-8")
    (target / "items.json").write_text("user items", encoding="utf-8")

    account = os.environ.get("USERDOMAIN", "") + "\\\\" + os.environ.get("USERNAME", "")
    denied = subprocess.run(
        ["icacls.exe", str(denied_file), "/deny", f"{account}:(R)"],
        capture_output=True,
        text=True,
        check=False,
    )
    if denied.returncode:
        pytest.skip("Could not configure an ACL-denied source file for rollback testing")
    try:
        result = _run_updater(package / "update_portable.ps1", source, target)
    finally:
        subprocess.run(
            ["icacls.exe", str(denied_file), "/remove:d", account],
            capture_output=True,
            check=False,
        )

    assert result.returncode != 0
    assert "Restoring previous program files" in result.stdout
    assert "Previous version restored" in result.stdout
    assert (target / "_internal" / "old.dll").read_bytes() == b"old runtime file"
    assert not (target / "_internal" / "a-new.dll").exists()
    assert (target / "ChestScanner.exe").read_bytes() == b"OLD-EXE"
    assert (target / "LICENSE").read_text(encoding="utf-8") == "old license"
    assert (target / "items.json").read_text(encoding="utf-8") == "user items"


def test_updater_has_backup_and_rollback_before_executable_replacement():
    updater = Path("update_portable.ps1").read_text(encoding="utf-8-sig")
    assert "ChestScanner-update-backup-" in updater
    assert "Restoring previous program files" in updater
    assert "Restore-Directory" in updater
    assert "Restore-File" in updater
    assert updater.index('$updateStarted = $true') < updater.index('Invoke-Robocopy -Arguments @((Join-Path $Source "_internal")')
    assert updater.index('Copy-Item -LiteralPath (Join-Path $Source "ChestScanner.exe")') > updater.index('Copy-Item -LiteralPath (Join-Path $Source "LICENSE")')


def test_build_script_uses_whitelisted_portable_packager():
    build = Path("build.bat").read_text(encoding="utf-8-sig")
    assert "build_portable.py --dist" in build
    assert "Compress-Archive" not in build


def test_release_build_entrypoints_exclude_prepopulated_item_database():
    build = Path("build.bat").read_text(encoding="utf-8-sig")
    installer = Path("installer.iss").read_text(encoding="utf-8-sig")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    portable = Path("build_portable.py").read_text(encoding="utf-8-sig")
    assert 'del /Q "%DIST%\\items.json"' in build
    assert 'copy /Y "items.json"' not in build
    assert '??????????? items.json ?? ???????' not in build
    assert not (Path(__file__).resolve().parents[1] / 'items.json').exists()
    assert 'items.json' not in portable
    assert 'dist\\ChestScanner\\items.json' not in installer
    assert 'Remove-Item dist/ChestScanner/items.json' in workflow
