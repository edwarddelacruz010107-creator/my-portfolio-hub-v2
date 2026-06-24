"""
scripts/normalize_skill_visibility.py
────────────────────────────────────
One-shot maintenance script to normalize skill visibility for existing rows.
Run this if old skills were inserted with a NULL `is_visible` value.

    python scripts/normalize_skill_visibility.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app
from app import db
from app.models.portfolio import Skill

with app.app_context():
    null_skills = db.session.query(Skill).filter(Skill.is_visible.is_(None)).all()
    count = len(null_skills)

    if count == 0:
        print('No skills with NULL is_visible found. No changes were made.')
    else:
        print(f'Found {count} skill(s) with NULL is_visible. Setting them to True...')
        db.session.query(Skill).filter(Skill.is_visible.is_(None)).update(
            {'is_visible': True}, synchronize_session='fetch'
        )
        db.session.commit()
        print(f'Updated {count} skill(s). All existing NULL is_visible values are now True.')
