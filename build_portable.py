"""Build a clean portable ZIP without packaging runtime user data."""
from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path


def _ignore_asset_custom(_directory: str, names: list[str]) -> set[str]:
    return {"custom"} if "custom" in names else set()


def build_portable_package(dist_dir: Path, project_root: Path, output_zip: Path) -> None:
    dist_dir = dist_dir.resolve()
    project_root = project_root.resolve()
    output_zip = output_zip.resolve()
    required = (
        dist_dir / "ChestScanner.exe",
        dist_dir / "_internal",
        project_root / "assets",
        project_root / "items.json",
        project_root / "LICENSE",
        project_root / "update_portable.bat",
        project_root / "update_portable.ps1",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing portable package inputs: " + ", ".join(missing))

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chestscanner-portable-", dir=output_zip.parent) as tmp:
        stage = Path(tmp)
        app_stage = stage / "ChestScanner"
        app_stage.mkdir()
        shutil.copy2(dist_dir / "ChestScanner.exe", app_stage / "ChestScanner.exe")
        shutil.copytree(dist_dir / "_internal", app_stage / "_internal")
        shutil.copytree(
            project_root / "assets",
            app_stage / "assets",
            ignore=_ignore_asset_custom,
        )
        shutil.copy2(project_root / "items.json", app_stage / "items.json")
        shutil.copy2(project_root / "LICENSE", app_stage / "LICENSE")
        shutil.copy2(project_root / "update_portable.bat", stage / "update_portable.bat")
        shutil.copy2(project_root / "update_portable.ps1", stage / "update_portable.ps1")

        temporary_zip = stage / "package.zip"
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file() and path != temporary_zip:
                    archive.write(path, path.relative_to(stage).as_posix())

        temporary_output = output_zip.with_name(output_zip.name + ".tmp")
        shutil.copy2(temporary_zip, temporary_output)
        temporary_output.replace(output_zip)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_portable_package(args.dist, Path(__file__).resolve().parent, args.output)
    print(f"Portable archive created: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
