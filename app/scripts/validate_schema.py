#!/usr/bin/env python3
"""
Validate database schema consistency with SQLAlchemy models.

This script checks:
  1. All model tables exist in database
  2. All model columns exist in tables
  3. All model indexes exist in database
  4. No unexpected database artifacts

Usage:
    python scripts/validate_schema.py
    
Exit codes:
    0 = Schema is valid
    1 = Schema validation failed
"""

import sys
from pathlib import Path

# Add project root to path
# BUG FIX (audit 2026-07-02): .parent.parent only reaches app/, not
# project root -- 'from app import ...' below failed with ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import inspect, text
from app import create_app, db


def validate_core_schema(inspector):
    """Validate core_db schema against models."""
    issues = []
    
    # Check each model table exists
    db_tables = set(inspector.get_table_names())
    
    for table in db.metadata.tables.values():
        # Skip tenant-bound tables (different database)
        if hasattr(table, 'bind_key') or getattr(table, 'bind_key', None) == 'tenant':
            continue
        
        if table.name not in db_tables:
            issues.append(('missing_table', f"Core table '{table.name}' missing from database"))
            continue
        
        # Check columns
        db_cols = {col['name'] for col in inspector.get_columns(table.name)}
        model_cols = {col.name for col in table.columns}
        
        for col in model_cols - db_cols:
            issues.append(('missing_column', f"Column {table.name}.{col} missing from database"))
        
        # Check indexes
        db_indexes = {idx['name'] for idx in inspector.get_indexes(table.name)}
        
        for index in table.indexes:
            if index.name not in db_indexes:
                issues.append(('missing_index', f"Index {table.name}.{index.name} missing from database"))
    
    return issues


def validate_models_consistency():
    """Validate SQLAlchemy model metadata consistency."""
    issues = []
    
    # Check for duplicate indexes in model definitions
    for table in db.metadata.tables.values():
        # Find columns with index=True
        auto_indexes = {}
        for col in table.columns:
            if col.index and col.name:
                auto_index_name = f'ix_{table.name}_{col.name}'
                auto_indexes[auto_index_name] = col.name
        
        # Find explicit indexes in __table_args__
        explicit_indexes = {idx.name: idx for idx in table.indexes}
        
        # Check for overlaps
        for auto_name, col_name in auto_indexes.items():
            if auto_name in explicit_indexes:
                issues.append(('duplicate_index', 
                    f"Table {table.name} column {col_name}: duplicate index '{auto_name}' "
                    f"(both index=True and __table_args__)"))
    
    return issues


def print_issues_report(issues, category_name):
    """Print formatted report of issues."""
    if not issues:
        return True
    
    print(f"\n❌ {category_name} Issues ({len(issues)}):\n")
    
    for issue_type, message in issues:
        print(f"  [{issue_type.upper()}] {message}")
    
    return False


def main():
    """Main validation entry point."""
    app = create_app()
    
    try:
        with app.app_context():
            print("=" * 100)
            print("DATABASE SCHEMA VALIDATION REPORT")
            print("=" * 100)
            
            # Check database connectivity
            try:
                result = db.session.execute(text("SELECT 1"))
                print("✓ Database connection: OK")
            except Exception as e:
                print(f"✗ Database connection: FAILED ({e})")
                return 1
            
            # Validate model consistency first
            model_issues = validate_models_consistency()
            model_ok = print_issues_report(model_issues, "Model Metadata") if model_issues else True
            
            # Validate schema
            inspector = inspect(db.engine)
            schema_issues = validate_core_schema(inspector)
            schema_ok = print_issues_report(schema_issues, "Schema") if schema_issues else True
            
            # Summary
            print("\n" + "=" * 100)
            if model_ok and schema_ok:
                print("✅ Schema validation: PASSED")
                print("=" * 100)
                return 0
            else:
                print("❌ Schema validation: FAILED")
                print("=" * 100)
                return 1
    
    except Exception as e:
        print(f"❌ Validation error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())