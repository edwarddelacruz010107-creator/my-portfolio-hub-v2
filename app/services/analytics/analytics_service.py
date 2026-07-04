from __future__ import annotations

class AnalyticsService:
    def summarize(self, data: dict) -> dict:
        return {'views': data.get('views', 0), 'visitors': data.get('visitors', 0)}
