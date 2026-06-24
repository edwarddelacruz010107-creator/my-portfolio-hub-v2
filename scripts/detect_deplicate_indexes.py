#!/usr/bin/env python3
"""
Detect duplicate index definitions in SQLAlchemy models.

This script identifies cases where a column has both:
  1. index=True attribute
  2. Explicit index in __table_args__

Which would cause PostgreSQL to reject duplicate index creation.

Usage:
    python scripts/detect_duplicate_indexes.py
    
Exit codes:
    0 = No issues found
    1 = Issues detected
"""

import re
import sys
from pathlib import Path


def analyze_models(models_dir='app/models'):
    """
    Scan model files for duplicate index patterns.
    
    Returns:
        list: List of issue dictionaries
    """
    models_path = Path(models_dir)
    if not models_path.exists():
        print(f"❌ Models directory not found: {models_dir}")
        return []
    
    issues = []
    
    for model_file in sorted(models_path.glob('*.py')):
        if model_file.name == '__init__.py':
            continue
        
        with open(model_file, encoding='utf-8') as f:
            content = f.read()
        
        # Find all class definitions that inherit from db.Model
        class_pattern = r'class\s+(\w+)\(db\.Model\):'
        
        for class_match in re.finditer(class_pattern, content):
            class_name = class_match.group(1)
            class_start = class_match.start()
            
            # Find the end of the class (next class or end of file)
            next_class = re.search(r'class\s+\w+\(', content[class_start + 1:])
            class_end = class_start + len(content[class_start:]) if not next_class else class_start + next_class.start()
            class_content = content[class_start:class_end]
            
            # Find __table_args__ definition
            table_args_pattern = r'__table_args__\s*=\s*\((.*?)\)'
            table_args_match = re.search(table_args_pattern, class_content, re.DOTALL)
            
            if not table_args_match:
                continue
            
            # Extract explicit indexes from __table_args__
            explicit_indexes = {}
            for idx_match in re.finditer(r"db\.Index\('([^']+)'", table_args_match.group(1)):
                index_name = idx_match.group(1)
                explicit_indexes[index_name] = idx_match.group(0)
            
            # Find columns with index=True
            col_pattern = r'(\w+)\s*=\s*db\.Column\([^)]*index=True[^)]*\)'
            
            for col_match in re.finditer(col_pattern, class_content):
                col_name = col_match.group(1)
                # SQLAlchemy generates index names as ix_<tablename>_<columnname>
                auto_index_name = f'ix_{class_name.lower()}_{col_name}'
                
                if auto_index_name in explicit_indexes:
                    # Get line number
                    line_num = content[:class_start + class_content.find(col_match.group(0))].count('\n') + 1
                    
                    issues.append({
                        'file': str(model_file),
                        'class': class_name,
                        'column': col_name,
                        'auto_index': auto_index_name,
                        'explicit_index': auto_index_name,
                        'line': line_num,
                        'problem': 'Duplicate: index=True creates auto index + explicit __table_args__',
                        'fix': f'Remove index=True from {col_name} column (keep __table_args__ definition)'
                    })
    
    return issues


def print_report(issues):
    """Print formatted report of issues found."""
    if not issues:
        print("✅ No duplicate index issues detected")
        return True
    
    print(f"❌ Found {len(issues)} duplicate index issue(s):\n")
    print("=" * 100)
    
    for i, issue in enumerate(issues, 1):
        print(f"\n{i}. {issue['file']}::{issue['class']}")
        print(f"   Line: {issue['line']}")
        print(f"   Column: {issue['column']}")
        print(f"   Index: {issue['auto_index']}")
        print(f"   Problem: {issue['problem']}")
        print(f"   Fix: {issue['fix']}")
    
    print("\n" + "=" * 100)
    print("\nRESULT: ❌ Schema validation FAILED")
    return False


def main():
    """Main entry point."""
    issues = analyze_models()
    success = print_report(issues)
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())