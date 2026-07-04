#!/usr/bin/env python
"""Verify hardening migration was applied."""
import sqlite3

db_path = 'storage/portfolio_core_dev.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check alembic_version
cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num DESC LIMIT 1")
version = cursor.fetchone()
print(f"Current migration: {version[0] if version else 'None'}")

# Check OTP TTL
cursor.execute("SELECT otp_expiry_minutes FROM global_email_config LIMIT 1")
ttl = cursor.fetchone()
print(f"OTP TTL: {ttl[0] if ttl else 'None'} minutes")

conn.close()
