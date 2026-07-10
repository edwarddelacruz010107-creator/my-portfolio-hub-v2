# Landing Hero True Live Editor

## Problem
The previous Landing CMS hero image controls changed sliders in a simplified 16:9 preview, but the editor did not reproduce the complete public landing hero browser card. It also did not support direct drag-to-position editing.

## Changes
- Replaced the simplified preview with a live browser-card preview matching the public landing hero.
- Added direct pointer/touch dragging inside the image frame.
- Added mouse-wheel zoom and keyboard fine-tuning.
- Added zoom-in, zoom-out, center, reset, and open-original controls.
- Added live preview binding for browser URL, profile caption, badge text, likes, views, comments, widgets, and animation setting.
- Added image loading, empty, and error states.
- Preserved the existing `hero_image_fit`, position X/Y, and zoom settings, so no database migration is required.
- Kept public rendering tied to the same persisted values, making the editor and public output consistent.

## Interaction
- Drag: reposition image.
- Mouse wheel: zoom.
- Arrow keys: move by 1%.
- Shift + arrow keys: move by 5%.
- `+` / `-`: zoom.
- Home: center image.

## Validation
- JavaScript syntax check passed.
- Python compilation passed.
- All application Jinja templates parsed successfully.
- Generated cache files removed.
