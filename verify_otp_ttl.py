#!/usr/bin/env python
"""Verify OTP TTL update."""
from app import create_app, db
from app.models.core import GlobalEmailConfig

app = create_app('default')
with app.app_context():
    cfg = GlobalEmailConfig.query.first()
    if cfg:
        print(f'✅ OTP TTL: {cfg.otp_expiry_minutes} minutes')
    else:
        print('❌ No GlobalEmailConfig found')
