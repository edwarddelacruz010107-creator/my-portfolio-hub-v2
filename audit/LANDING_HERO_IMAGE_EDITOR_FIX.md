# Landing Hero Image Editor Fix

Added a production-safe, database-backed image frame editor to Superadmin → Landing CMS.

## Features

- Replace/remove hero image through the existing Cloudinary-enabled uploader.
- Exact 16:9 browser-frame preview.
- Cover or contain fit mode.
- Horizontal and vertical focus controls.
- 100–180% zoom control.
- Reset frame action and open-original action.
- Live preview updates after upload, URL edits, and slider changes.
- Draft/published settings stored through PlatformSetting; no schema migration required.
- Public landing page applies the saved fit, focus, and zoom values.
- Preserves valid 0% focus values during autosave and form submission.
- Founder crop editor now correctly clears its preview when the image URL is removed.
