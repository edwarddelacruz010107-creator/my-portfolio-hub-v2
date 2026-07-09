"""Convert existing local portfolio uploads to WebP.

Usage:
    python scripts/convert_uploads_to_webp.py --dry-run
    python scripts/convert_uploads_to_webp.py --apply
    python scripts/convert_uploads_to_webp.py --apply --tenant administrator
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.media.webp_backfill_service import convert_existing_uploads_to_webp


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert existing local image uploads to WebP.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files or DB records.")
    mode.add_argument("--apply", action="store_true", help="Convert files and update DB records.")
    parser.add_argument("--tenant", dest="tenant_slug", help="Optional tenant slug to limit conversion.")
    parser.add_argument("--quality", type=int, default=None, help="WebP quality 1-100. Defaults to UPLOAD_WEBP_QUALITY or 82.")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        result = convert_existing_uploads_to_webp(
            tenant_slug=args.tenant_slug,
            dry_run=not args.apply,
            quality=args.quality,
        )
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
