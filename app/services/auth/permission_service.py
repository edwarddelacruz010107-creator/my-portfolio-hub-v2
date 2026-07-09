from __future__ import annotations


class PermissionService:
    def __init__(self) -> None:
        self.roles = {'tenant_admin': {'can_publish': True, 'can_upload': True}, 'editor': {'can_publish': True, 'can_upload': True}, 'readonly': {'can_publish': False, 'can_upload': False}}

    def permissions_for(self, role: str | None) -> dict:
        return self.roles.get(role or 'readonly', self.roles['readonly'])
