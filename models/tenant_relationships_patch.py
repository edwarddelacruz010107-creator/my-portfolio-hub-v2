"""
models/tenant_relationships_patch.py

Add these two relationship lines to your existing Tenant model class.
Locate the Tenant class in models/tenant.py (or models/__init__.py) and
add the following inside the class body — alongside existing back_populates entries.

DO NOT replace the entire Tenant model. This is a targeted patch.
"""

# ── Paste these two lines into the Tenant model class body ──────────────────

# certificates = db.relationship(
#     "Certificate",
#     back_populates="tenant",
#     cascade="all, delete-orphan",
#     order_by="Certificate.sort_order.asc(), Certificate.issue_date.desc()",
#     lazy="dynamic",
# )
#
# badges = db.relationship(
#     "Badge",
#     back_populates="tenant",
#     cascade="all, delete-orphan",
#     order_by="Badge.display_order.asc()",
#     lazy="dynamic",
# )

# ── Paste this import at the top of models/tenant.py ────────────────────────
# from models.certificates import Certificate, Badge  # noqa: F401 (needed for relationship resolution)
