from pathlib import Path

import pytest

from chest_scanner import paths
from chest_scanner.commons import absolute_path


@pytest.fixture
def app_paths(tmp_path, monkeypatch):
    base = tmp_path / "Application Folder"
    profile = base / "profiles" / "Profile One"
    monkeypatch.setattr(paths, "BASE_DIR", str(base))
    monkeypatch.setattr(paths, "PROFILE_DIR", str(profile))
    return base, profile


def test_profile_asset_paths_support_both_separator_styles(app_paths):
    _base, profile = app_paths
    expected = str(profile / "items" / "Diamond.png")

    assert absolute_path("items/Diamond.png") == expected
    assert absolute_path(r"items\Diamond.png") == expected
    assert absolute_path("./items/Diamond.png") == expected


def test_unknown_item_assets_resolve_inside_active_profile(app_paths):
    _base, profile = app_paths

    assert absolute_path(r"unknown_items\Unknown.png") == str(
        profile / "unknown_items" / "Unknown.png")


def test_other_relative_paths_resolve_from_application_root(app_paths):
    base, _profile = app_paths

    assert absolute_path(r"assets\chest_open.png") == str(
        base / "assets" / "chest_open.png")


def test_parent_directory_is_not_stripped_during_resolution(app_paths):
    base, _profile = app_paths

    assert absolute_path("../items/external.png") == str(
        base / ".." / "items" / "external.png")


def test_pathlike_and_empty_values_are_supported(app_paths):
    _base, profile = app_paths

    assert absolute_path(Path("items") / "icon.png") == str(
        profile / "items" / "icon.png")
    assert absolute_path(None) is None
    assert absolute_path("") is None


def test_windows_absolute_path_is_not_joined_to_profile(app_paths):
    path = r"C:\Old Drive\items\Diamond.png"

    assert absolute_path(path) == path


def test_relative_photo_reference_survives_application_relocation(
        tmp_path, monkeypatch):
    first_root = tmp_path / "Old Location"
    first_profile = first_root / "profiles" / "Default"
    first_photo = first_profile / "items" / "Diamond.png"
    first_photo.parent.mkdir(parents=True)
    first_photo.write_bytes(b"image")
    monkeypatch.setattr(paths, "BASE_DIR", str(first_root))
    monkeypatch.setattr(paths, "PROFILE_DIR", str(first_profile))
    stored_reference = "items/Diamond.png"
    assert Path(absolute_path(stored_reference)).is_file()

    moved_root = tmp_path / "New Location"
    moved_profile = moved_root / "profiles" / "Default"
    moved_photo = moved_profile / "items" / "Diamond.png"
    moved_photo.parent.mkdir(parents=True)
    moved_photo.write_bytes(b"image")
    monkeypatch.setattr(paths, "BASE_DIR", str(moved_root))
    monkeypatch.setattr(paths, "PROFILE_DIR", str(moved_profile))

    assert Path(absolute_path(stored_reference)) == moved_photo
    assert Path(absolute_path(stored_reference)).is_file()
