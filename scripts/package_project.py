"""Packaging script to create clean distributed-rate-limiter.zip archive.

Includes the full git history (.git) while excluding virtual environments (.venv),
compiled bytecode (__pycache__), pytest caches (.pytest_cache), local .env files,
and downloaded test binaries.
"""

import os
import zipfile
from pathlib import Path


def package_project():
    root_dir = Path(__file__).resolve().parent.parent
    output_zip = root_dir.parent / "distributed-rate-limiter.zip"

    exclude_patterns = {
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
        "env",
        ".coverage",
        "htmlcov",
        ".env",
        "bin",  # downloaded test redis binary in tests/bin
    }

    print(f"Creating archive at: {output_zip}")
    file_count = 0

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(root_dir):
            rel_root = Path(root).relative_to(root_dir)

            # Skip excluded directory paths
            parts = rel_root.parts
            if any(part in exclude_patterns for part in parts):
                continue

            for file in files:
                # Skip excluded files
                if file.endswith((".pyc", ".pyo", ".pyd")) or file in (".env", "redis-server.exe"):
                    continue

                full_path = Path(root) / file
                archive_name = Path("distributed-rate-limiter") / rel_root / file
                zipf.write(full_path, str(archive_name))
                file_count += 1

    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print(f"Packaged {file_count} files successfully! Archive size: {size_mb:.2f} MB")


if __name__ == "__main__":
    package_project()
