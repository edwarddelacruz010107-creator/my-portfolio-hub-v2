import os
import sys
from sqlalchemy import inspect

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

os.environ['FLASK_ENV'] = 'development'
from app import create_app, db

app = create_app('default')
with app.app_context():
    engine = db.get_engine(bind_key='tenant')
    insp = inspect(engine)
    print('engine', engine.url)
    print('has_projects', insp.has_table('projects'))
    print('project_cols', [c['name'] for c in insp.get_columns('projects')] if insp.has_table('projects') else [])
    print('has_project_reactions', insp.has_table('project_reactions'))
    print('reaction_cols', [c['name'] for c in insp.get_columns('project_reactions')] if insp.has_table('project_reactions') else [])
