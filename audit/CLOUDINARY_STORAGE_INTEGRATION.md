# Cloudinary Storage Integration

## Scope

Cloudinary is available as a first-class persistent image provider for:

- profile photos
- project screenshots
- testimonial avatars
- certificate images and badges
- landing-page hero/founder images
- theme catalog thumbnails, banners, and previews

The common upload flow validates image content, converts supported images to WebP, uploads through the Cloudinary Python SDK, and stores the returned HTTPS CDN URL in the existing database field.

## Production variables

```env
STORAGE_PROVIDER=cloudinary
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret
CLOUDINARY_FOLDER_ROOT=myportfoliohub
CONVERT_UPLOADS_TO_WEBP=true
UPLOAD_WEBP_QUALITY=82
UPLOAD_IMAGE_MAX_DIMENSION=2048
```

`CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME` is also supported instead of the three separate credential variables.

## Security and reliability

- No credentials are hardcoded.
- Production fails fast when Cloudinary is selected but credentials are incomplete.
- Cloudinary images are allowed by the Content Security Policy.
- Deletes are restricted to the configured Cloudinary folder root.
- Existing Supabase URLs remain readable and deletable after switching providers.
- Existing local files are not automatically migrated; re-upload or migrate them before the old filesystem is removed.

## Connectivity test

```bash
python scripts/test_cloudinary_storage.py
```

The test uploads a generated WebP health-check image and deletes it immediately.
