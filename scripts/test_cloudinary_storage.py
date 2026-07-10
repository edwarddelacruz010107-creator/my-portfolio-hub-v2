#!/usr/bin/env python3
"""Cloudinary connectivity smoke test.

Run from the project root after setting production-like Cloudinary variables:
    python scripts/test_cloudinary_storage.py

The script uploads a generated 32x32 WebP into the configured folder and then
removes it. It never prints the API secret.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from PIL import Image
        import cloudinary
        import cloudinary.uploader
    except ImportError as exc:
        print(f"Missing dependency: {exc}")
        return 2

    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()
    if not all((cloud_name, api_key, api_secret)):
        print("Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET first.")
        return 2

    cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (80, 100, 240)).save(buf, "WEBP", quality=80)
    buf.seek(0)

    root = os.environ.get("CLOUDINARY_FOLDER_ROOT", "myportfoliohub").strip("/")
    result = cloudinary.uploader.upload(
        buf,
        resource_type="image",
        folder=f"{root}/healthchecks",
        public_id="storage-connectivity-test",
        overwrite=True,
        invalidate=True,
        format="webp",
    )
    public_id = result.get("public_id")
    secure_url = result.get("secure_url")
    print(f"Upload OK: {secure_url}")
    deletion = cloudinary.uploader.destroy(public_id, resource_type="image", invalidate=True)
    print(f"Delete result: {deletion.get('result')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
