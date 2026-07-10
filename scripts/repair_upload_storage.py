"""Audit and repair local upload storage references.

Usage:
    python scripts/repair_upload_storage.py --dry-run
    python scripts/repair_upload_storage.py --copy

This script checks database image fields for profile, project, testimonial,
certificate, badge, billing QR, and payment proof uploads. If a referenced file
exists in a legacy upload root but not in the configured persistent
UPLOAD_FOLDER, --copy copies it into the persistent root. Missing files are
reported clearly; they cannot be restored from the database filename alone.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from app import create_app, db
from app.models.portfolio import Profile, Project, Testimonial, Certificate, PaymentMethod, PaymentSubmission
from app.services.media.upload_storage import (
    build_upload_url,
    candidate_upload_roots,
    ensure_upload_folder,
    is_remote_url,
    normalize_upload_reference,
    primary_upload_root,
    resolve_upload_file,
)


def _records():
    for obj in Profile.query.all():
        if obj.profile_image:
            yield "Profile", obj.id, "profile_image", obj.profile_image, "profiles"
    for obj in Project.query.all():
        if obj.image:
            yield "Project", obj.id, "image", obj.image, "projects"
    for obj in Testimonial.query.all():
        if obj.author_avatar:
            yield "Testimonial", obj.id, "author_avatar", obj.author_avatar, "profiles"
    for obj in Certificate.query.all():
        if obj.image_path:
            yield "Certificate", obj.id, "image_path", obj.image_path, "certificates"
        if obj.badge_path:
            yield "Certificate", obj.id, "badge_path", obj.badge_path, "certificates"
    for obj in PaymentMethod.query.all():
        if obj.qr_image:
            yield "PaymentMethod", obj.id, "qr_image", obj.qr_image, "billing"
    for obj in PaymentSubmission.query.all():
        if obj.payment_proof:
            yield "PaymentSubmission", obj.id, "payment_proof", obj.payment_proof, "billing"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--copy", action="store_true", help="Copy legacy files into UPLOAD_FOLDER when found")
    parser.add_argument("--dry-run", action="store_true", help="Only report; do not copy")
    parser.add_argument("--config", default="default", help="Flask config name")
    args = parser.parse_args()

    app = create_app(args.config)
    with app.app_context():
        root = primary_upload_root()
        print(f"Primary upload root: {root}")
        print("Search roots:")
        for r in candidate_upload_roots():
            print(f"  - {r}")
        print()

        total = found = copied = missing = remote = 0
        for model, obj_id, field, value, folder in _records():
            total += 1
            if is_remote_url(value):
                remote += 1
                print(f"REMOTE  {model}#{obj_id}.{field}: {value}")
                continue

            normalized = normalize_upload_reference(value, folder)
            if not normalized:
                missing += 1
                print(f"BADREF  {model}#{obj_id}.{field}: {value!r}")
                continue

            actual = resolve_upload_file(*normalized)
            if not actual:
                missing += 1
                print(f"MISSING {model}#{obj_id}.{field}: {value!r}")
                continue

            found += 1
            target_dir = ensure_upload_folder(folder)
            target = target_dir / normalized[1]
            if actual.resolve() != target.resolve():
                if args.copy and not args.dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(actual, target)
                    copied += 1
                    print(f"COPIED  {model}#{obj_id}.{field}: {actual} -> {target}")
                else:
                    print(f"LEGACY  {model}#{obj_id}.{field}: {actual} -> {target}")
            else:
                print(f"OK      {model}#{obj_id}.{field}: {build_upload_url(value, folder)}")

        print()
        print(f"Total refs: {total}")
        print(f"Found local: {found}")
        print(f"Remote refs: {remote}")
        print(f"Copied: {copied}")
        print(f"Missing/bad refs: {missing}")
        if missing:
            print("\nMissing files cannot be rebuilt from database filenames. Re-upload those images or restore them from a backup/object storage.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
