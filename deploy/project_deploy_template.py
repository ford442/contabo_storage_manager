#!/usr/bin/env python3
"""
project_deploy_template.py

Copy this file into your project as `deploy.py` (or deploy_contabo.py).
Customize the constants at the top for your project.

Usage:
  1. Build your project:  npm run build   (or python build, etc.)
  2. python deploy.py

This script contacts https://storage.noahcohn.com (your Contabo storage manager)
to upload your entire build as a single zip archive.  The server extracts it and
pushes all files over one persistent SFTP connection — much faster than uploading
files individually.

Actual FTP/SFTP credentials never leave the VPS.

Requirements:
  pip install requests
"""

import io
import os
import sys
import zipfile
from pathlib import Path
from typing import Optional

import requests

# ============================================================
# PER-PROJECT CONFIGURATION - EDIT THESE
# ============================================================
PROJECT_NAME: str = "your-project-name"   # e.g. "the-jokesters", "CandyWorld"
BUILD_DIR: str = "dist"                   # usually "dist", "build", or "out"
CONTABO_BASE_URL: str = "https://storage.noahcohn.com"

# Optional deploy token (recommended for security).
# Set via environment: export DEPLOY_TOKEN="your_long_token_from_vps_env"
DEPLOY_TOKEN: Optional[str] = os.getenv("DEPLOY_TOKEN")

# Optional deploy target: "test" (default → test.1ink.us) or "go" (→ go.1ink.us)
# Set via environment: export DEPLOY_TARGET=go
# Requires DEPLOY_BASE_DIR_GO to be configured on the VPS for the "go" target to work.
DEPLOY_TARGET: str = os.getenv("DEPLOY_TARGET", "test")
# ============================================================


def build_zip(build_path: Path) -> bytes:
    """Zip the contents of build_path into an in-memory archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(build_path.rglob("*")):
            if file.is_dir():
                continue
            rel = file.relative_to(build_path)
            # Skip common junk
            parts = rel.parts
            if any(p in (".git", "node_modules", "__pycache__") for p in parts):
                continue
            zf.write(file, str(rel))
            print(f"  + {rel}")
    return buf.getvalue()


def deploy_bundle(build_path: Path) -> bool:
    """Zip the build and upload it as a single bundle."""
    url = f"{CONTABO_BASE_URL}/api/deploy/{PROJECT_NAME}/bundle"
    headers = {}
    if DEPLOY_TOKEN:
        headers["X-Deploy-Token"] = DEPLOY_TOKEN

    # Build form data so that DEPLOY_TARGET=go (or target_site) is respected
    form_data = {"target_site": DEPLOY_TARGET}
    # If user wants a different folder under the target (e.g. "yoga" under go.1ink.us/),
    # they can set TARGET_FOLDER env or edit here.
    target_folder = os.getenv("TARGET_FOLDER")
    if target_folder:
        form_data["target_folder"] = target_folder

    print("Building zip archive...")
    zip_bytes = build_zip(build_path)
    print(f"Archive size: {len(zip_bytes) / 1024:.1f} KB\n")

    print(f"Uploading bundle to target '{DEPLOY_TARGET}' ...")
    try:
        response = requests.post(
            url,
            files={"bundle": ("build.zip", zip_bytes, "application/zip")},
            data=form_data,
            headers=headers,
            timeout=300,
        )
    except Exception as exc:
        print(f"  ✗ Upload exception: {exc}")
        return False

    if response.status_code == 200:
        data = response.json()
        print(f"  ✓ {data.get('uploaded', 0)} files uploaded")
        if data.get("failed"):
            print("  Failures:")
            for f in data["failed"]:
                print(f"    ✗ {f['path']}: {f['error']}")
        return not data.get("failed")
    else:
        print(f"  ✗ {response.status_code}: {response.text[:400]}")
        return False


def main():
    target_host = "go.1ink.us" if DEPLOY_TARGET == "go" else "test.1ink.us"
    print(f"\n=== Deploying '{PROJECT_NAME}' via Contabo -> {target_host} (target={DEPLOY_TARGET}) ===\n")

    build_path = Path(BUILD_DIR)
    if not build_path.exists() or not build_path.is_dir():
        print(f"ERROR: Build directory '{BUILD_DIR}/' does not exist.")
        print("Please run your build command first (e.g. `npm run build`).")
        sys.exit(1)

    try:
        health = requests.get(f"{CONTABO_BASE_URL}/api/deploy/health", timeout=10)
        if health.status_code == 200:
            print(f"Contabo deploy service: {health.json().get('status', 'unknown')}")
    except Exception:
        print("Warning: Could not contact storage.noahcohn.com (continuing anyway).")

    print()
    success = deploy_bundle(build_path)

    print(f"\n=== {'Deployment complete' if success else 'Deployment finished with errors'} ===")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
