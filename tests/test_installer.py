"""Installer rules that protect user-owned profile data."""
from pathlib import Path


def test_installer_preserves_existing_item_database_on_upgrade_and_uninstall():
    installer = Path(__file__).resolve().parents[1] / "installer.iss"
    lines = installer.read_text(encoding="utf-8").splitlines()
    item_entries = [
        line for line in lines
        if line.strip().startswith('Source: "dist\\ChestScanner\\items.json"')
    ]
    assert len(item_entries) == 1
    flags = item_entries[0].split("Flags:", 1)[1].strip().split()
    assert "onlyifdoesntexist" in flags
    assert "uninsneveruninstall" in flags



def test_release_version_is_consistent_across_exe_and_installer_metadata():
    import re

    root = Path(__file__).resolve().parents[1]
    installer = (root / "installer.iss").read_text(encoding="utf-8-sig")
    version_info = (root / "version_info.txt").read_text(encoding="utf-8-sig")
    release_notes = (root / "release_notes.md").read_text(encoding="utf-8-sig")

    installer_match = re.search(r'^#define MyAppVersion "([^"]+)"', installer, re.MULTILINE)
    file_match = re.search(r"StringStruct\('FileVersion', '([^']+)'\)", version_info)
    product_match = re.search(r"StringStruct\('ProductVersion', '([^']+)'\)", version_info)
    assert installer_match
    assert file_match
    assert product_match
    version = installer_match.group(1)
    assert file_match.group(1) == version
    assert product_match.group(1) == version
    assert re.search(rf"^## {re.escape(version)} \(", release_notes, re.MULTILINE)


def test_ci_has_a_packaging_job_for_installer_and_portable_archive():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "  package:" in workflow
    assert "build_portable.py" in workflow
    assert "ISCC.exe" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "ChestScanner-release-" + chr(36) + "{{ github.sha }}" in workflow
    assert "ruff check chest_scanner tests build_portable.py" in workflow
