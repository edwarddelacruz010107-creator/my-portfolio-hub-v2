from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TenantContext:
    tenant: Any = None
    profile: Any = None
    subscription_state: str = 'trial'
    plan: str = 'starter'
    features: dict[str, bool] = field(default_factory=dict)
    permissions: dict[str, bool] = field(default_factory=dict)
    subscription_badge: str = 'Trial'
    trial_days_left: int = 0
