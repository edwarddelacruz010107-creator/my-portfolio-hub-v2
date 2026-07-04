from __future__ import annotations

class MediaService:
    def validate_upload(self, filename: str) -> bool:
        return bool(filename and '.' in filename)
