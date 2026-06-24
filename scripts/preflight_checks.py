#!/usr/bin/env python3
"""
portfolio_cms_v5_3_preflight_checks.py

Pre-flight validation script for Portfolio CMS v5.3 database remediation.
Runs comprehensive checks before deploying to production.

Usage:
    python3 portfolio_cms_v5_3_preflight_checks.py --env production
    python3 portfolio_cms_v5_3_preflight_checks.py --env development
    python3 portfolio_cms_v5_3_preflight_checks.py --check all
    python3 portfolio_cms_v5_3_preflight_checks.py --check migrations
"""

import os
import sys
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

class Color:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

class ValidationResult:
    """Stores individual check result."""
    def __init__(self, name: str, passed: bool, message: str, severity: str = "info"):
        self.name = name
        self.passed = passed
        self.message = message
        self.severity = severity  # info, warning, error
        self.timestamp = datetime.now()

class PreflightValidator:
    """Main validator class."""
    
    def __init__(self, project_root: Optional[str] = None):
        self.project_root = Path(project_root or os.getcwd())
        self.results: List[ValidationResult] = []
        self.checks_run = 0
        self.checks_passed = 0
        
    def add_result(self, name: str, passed: bool, message: str, severity: str = "info"):
        """Record a check result."""
        self.results.append(ValidationResult(name, passed, message, severity))
        self.checks_run += 1
        if passed:
            self.checks_passed += 1
        
        # Print immediately
        status = f"{Color.GREEN}✓ PASS{Color.END}" if passed else f"{Color.RED}✗ FAIL{Color.END}"
        print(f"  {status} {name}: {message}")
    
    def print_header(self, title: str):
        """Print section header."""
        print(f"\n{Color.BLUE}{'='*70}{Color.END}")
        print(f"{Color.BLUE}{title}{Color.END}")
        print(f"{Color.BLUE}{'='*70}{Color.END}")
    
    def print_summary(self):
        """Print summary of all checks."""
        self.print_header("SUMMARY")
        passed = self.checks_passed
        total = self.checks_run
        pct = (passed / total * 100) if total > 0 else 0
        
        status_color = Color.GREEN if passed == total else Color.RED
        print(f"\n{status_color}Results: {passed}/{total} checks passed ({pct:.0f}%){Color.END}\n")
        
        # Group by severity
        errors = [r for r in self.results if not r.passed and r.severity == "error"]
        warnings = [r for r in self.results if not r.passed and r.severity == "warning"]
        
        if errors:
            print(f"{Color.RED}Critical Errors ({len(errors)}):{Color.END}")
            for r in errors:
                print(f"  • {r.name}: {r.message}")
        
        if warnings:
            print(f"{Color.YELLOW}Warnings ({len(warnings)}):{Color.END}")
            for r in warnings:
                print(f"  • {r.name}: {r.message}")
        
        return passed == total
    
    # ────────────────────────────────────────────────────────────────────────────
    # Code Structure Checks
    # ────────────────────────────────────────────────────────────────────────────
    
    def check_file_modifications(self):
        """Verify all required files have been modified."""
        self.print_header("CODE MODIFICATIONS")
        
        required_changes = {
            'app/__init__.py': ['db.get_engine(bind_key=', 'tenant_engine'],
            'app/heartbeat/__init__.py': ['db.get_engine(bind_key'],
            'migrations/env.py': ['include_object', 'table.info'],
            'migrations/tenant/env.py': ['Profile, Skill, Project, Testimonial, Service'],
            'wsgi.py': ['create_app'],
        }
        
        for filepath, required_strs in required_changes.items():
            full_path = self.project_root / filepath
            if not full_path.exists():
                self.add_result(
                    f"File exists: {filepath}",
                    False,
                    f"File not found at {full_path}",
                    "error"
                )
                continue
            
            content = full_path.read_text()
            missing = [s for s in required_strs if s not in content]
            
            if missing:
                self.add_result(
                    f"Code changes: {filepath}",
                    False,
                    f"Missing expected code: {missing}",
                    "error"
                )
            else:
                self.add_result(
                    f"Code changes: {filepath}",
                    True,
                    "All required changes present"
                )
    
    def check_no_deprecated_apis(self):
        """Verify deprecated APIs have been removed."""
        self.print_header("DEPRECATED API REMOVAL")
        
        files_to_check = [
            'app/__init__.py',
            'app/heartbeat/__init__.py',
            'wsgi.py',
        ]
        
        deprecated_patterns = {
            'db.engines[': 'db.engines dictionary (Flask-SQLAlchemy 3.x incompatible)',
            'db.get_engine(bind=': 'db.get_engine(bind=...) (use bind_key= instead)',
            'ProxyFix(app.wsgi_app' in open(self.project_root / 'wsgi.py').read() if (self.project_root / 'wsgi.py').exists() else False: 'ProxyFix in wsgi.py',
        }
        
        for filepath in files_to_check:
            full_path = self.project_root / filepath
            if not full_path.exists():
                continue
            
            content = full_path.read_text()
            
            # Check for db.engines usage (except in comments)
            if 'db.engines' in content:
                lines = [l for l in content.split('\n') if 'db.engines' in l and not l.strip().startswith('#')]
                if lines:
                    self.add_result(
                        f"No deprecated db.engines: {filepath}",
                        False,
                        f"Still contains db.engines in {len(lines)} places",
                        "error"
                    )
                else:
                    self.add_result(
                        f"No deprecated db.engines: {filepath}",
                        True,
                        "Found only in comments"
                    )
            else:
                self.add_result(
                    f"No deprecated db.engines: {filepath}",
                    True,
                    "No deprecated APIs found"
                )
        
        # Check ProxyFix in wsgi.py
        wsgi_path = self.project_root / 'wsgi.py'
        if wsgi_path.exists():
            wsgi_content = wsgi_path.read_text()
            if 'ProxyFix' in wsgi_content:
                self.add_result(
                    "ProxyFix removal (wsgi.py)",
                    False,
                    "ProxyFix still imported/used in wsgi.py",
                    "error"
                )
            else:
                self.add_result(
                    "ProxyFix removal (wsgi.py)",
                    True,
                    "ProxyFix correctly removed from wsgi.py"
                )
    
    # ────────────────────────────────────────────────────────────────────────────
    # Migration Checks
    # ────────────────────────────────────────────────────────────────────────────
    
    def check_migration_files(self):
        """Verify migration files are correctly structured."""
        self.print_header("MIGRATION FILES")
        
        migrations_dir = self.project_root / 'migrations' / 'versions'
        
        if not migrations_dir.exists():
            self.add_result(
                "Migrations directory exists",
                False,
                "migrations/versions/ directory not found",
                "error"
            )
            return
        
        self.add_result(
            "Migrations directory exists",
            True,
            f"Found {len(list(migrations_dir.glob('*.py')))} migration files"
        )
        
        # Check for deleted file
        bad_0027 = migrations_dir / '0027_inquiry_delivery_fields.py'
        if bad_0027.exists():
            self.add_result(
                "0027_inquiry_delivery_fields deleted",
                False,
                "Incomplete duplicate migration still present",
                "error"
            )
        else:
            self.add_result(
                "0027_inquiry_delivery_fields deleted",
                True,
                "Duplicate migration successfully removed"
            )
        
        # Check for good 0027
        good_0027 = migrations_dir / '0027_contact_delivery_fields.py'
        if good_0027.exists():
            self.add_result(
                "0027_contact_delivery_fields exists",
                True,
                "Superset migration present"
            )
        else:
            self.add_result(
                "0027_contact_delivery_fields exists",
                False,
                "Superset migration not found",
                "error"
            )
        
        # Check for new 0028
        new_0028 = migrations_dir / '0028_add_email_only_provider.py'
        if new_0028.exists():
            self.add_result(
                "0028_add_email_only_provider created",
                True,
                "New enum provider migration present"
            )
            # Verify idempotent
            content = new_0028.read_text()
            if 'IF NOT EXISTS' in content:
                self.add_result(
                    "0028 is idempotent",
                    True,
                    "Uses IF NOT EXISTS clause"
                )
            else:
                self.add_result(
                    "0028 is idempotent",
                    False,
                    "Missing IF NOT EXISTS safety clause",
                    "warning"
                )
        else:
            self.add_result(
                "0028_add_email_only_provider created",
                False,
                "New migration not found",
                "error"
            )
        
        # Check for retired 003
        retired_003 = migrations_dir / '_RETIRED_003_tenant_communication_settings.py.bak'
        orphaned_003 = migrations_dir / '003_tenant_communication_settings.py'
        
        if orphaned_003.exists():
            self.add_result(
                "003 orphaned migration retired",
                False,
                "Orphaned migration still active",
                "error"
            )
        elif retired_003.exists():
            self.add_result(
                "003 orphaned migration retired",
                True,
                "Successfully retired to backup"
            )
        else:
            self.add_result(
                "003 orphaned migration retired",
                True,
                "Orphaned migration not found"
            )
    
    def check_migration_revision_ids(self):
        """Verify migration revision IDs are unique and well-formed."""
        self.print_header("MIGRATION REVISION IDS")
        
        migrations_dir = self.project_root / 'migrations' / 'versions'
        revisions = {}
        
        for mig_file in sorted(migrations_dir.glob('*.py')):
            if mig_file.name.startswith('_RETIRED'):
                continue
            if mig_file.name.startswith('__'):
                continue
            
            content = mig_file.read_text()
            # Extract revision ID
            for line in content.split('\n'):
                if line.startswith("revision"):
                    match = line.split("=")[1].strip().strip("'\"")
                    if match not in revisions:
                        revisions[match] = []
                    revisions[match].append(mig_file.name)
        
        # Check for duplicates
        duplicates = {r: files for r, files in revisions.items() if len(files) > 1}
        if duplicates:
            for rev, files in duplicates.items():
                self.add_result(
                    f"Unique revision ID: {rev}",
                    False,
                    f"Duplicate in files: {files}",
                    "error"
                )
        else:
            self.add_result(
                "All revision IDs unique",
                True,
                f"Verified {len(revisions)} migration revisions"
            )
    
    # ────────────────────────────────────────────────────────────────────────────
    # Database Configuration Checks
    # ────────────────────────────────────────────────────────────────────────────
    
    def check_database_config(self):
        """Verify database configuration is present."""
        self.print_header("DATABASE CONFIGURATION")
        
        config_path = self.project_root / 'config.py'
        if not config_path.exists():
            self.add_result(
                "config.py exists",
                False,
                "Configuration file not found",
                "error"
            )
            return
        
        config_content = config_path.read_text()
        
        required_configs = [
            ('CORE_DATABASE_URL' in config_content or 'DATABASE_URL' in config_content, 'CORE_DATABASE_URL'),
            ('TENANT_DATABASE_URL' in config_content, 'TENANT_DATABASE_URL'),
            ('SQLALCHEMY_BINDS' in config_content, 'SQLALCHEMY_BINDS'),
        ]
        
        for found, name in required_configs:
            self.add_result(
                f"Config defines {name}",
                found,
                f"Expected configuration present" if found else f"Missing {name}"
            )
    
    def check_model_bindings(self):
        """Verify models have correct bind_key definitions."""
        self.print_header("MODEL BIND KEYS")
        
        tenant_models_path = self.project_root / 'app' / 'models' / 'tenant_data.py'
        if not tenant_models_path.exists():
            self.add_result(
                "tenant_data.py exists",
                False,
                "Tenant models file not found",
                "error"
            )
            return
        
        content = tenant_models_path.read_text()
        tenant_bound_models = ['Profile', 'Skill', 'Project', 'Testimonial', 'Service']
        
        for model in tenant_bound_models:
            if f"class {model}" in content:
                # Check for __bind_key__ = 'tenant'
                if f"__bind_key__ = 'tenant'" in content[content.find(f"class {model}"):content.find(f"class {model}") + 500]:
                    self.add_result(
                        f"Model bind key: {model}",
                        True,
                        "Correctly bound to tenant database"
                    )
                else:
                    self.add_result(
                        f"Model bind key: {model}",
                        False,
                        "Missing __bind_key__ = 'tenant'",
                        "warning"
                    )
            else:
                self.add_result(
                    f"Model exists: {model}",
                    False,
                    f"{model} not found in tenant_data.py",
                    "error"
                )
    
    # ────────────────────────────────────────────────────────────────────────────
    # Documentation Checks
    # ────────────────────────────────────────────────────────────────────────────
    
    def check_documentation(self):
        """Verify remediation documentation is present."""
        self.print_header("DOCUMENTATION")
        
        doc_files = [
            ('DATABASE_RELIABILITY_REMEDIATION_REPORT.md', 'Remediation report'),
            ('MIGRATION_RESOLUTION_NOTES.txt', 'Migration resolution notes'),
            ('DUPLICATE_MIGRATION_ANALYSIS.txt', 'Duplicate migration analysis'),
        ]
        
        for filename, description in doc_files:
            path = self.project_root / filename
            if path.exists():
                size = path.stat().st_size
                self.add_result(
                    f"Documentation: {description}",
                    True,
                    f"File present ({size} bytes)"
                )
            else:
                self.add_result(
                    f"Documentation: {description}",
                    False,
                    f"File not found: {filename}",
                    "warning"
                )
    
    # ────────────────────────────────────────────────────────────────────────────
    # Run All Checks
    # ────────────────────────────────────────────────────────────────────────────
    
    def run_all_checks(self, specific_check: Optional[str] = None):
        """Run all validation checks."""
        print(f"\n{Color.BLUE}Portfolio CMS v5.3 Database Remediation Pre-flight Checks{Color.END}")
        print(f"Project root: {self.project_root}")
        print(f"Started: {datetime.now().isoformat()}\n")
        
        if specific_check is None or specific_check == 'code':
            self.check_file_modifications()
            self.check_no_deprecated_apis()
        
        if specific_check is None or specific_check == 'migrations':
            self.check_migration_files()
            self.check_migration_revision_ids()
        
        if specific_check is None or specific_check == 'config':
            self.check_database_config()
            self.check_model_bindings()
        
        if specific_check is None or specific_check == 'docs':
            self.check_documentation()
        
        return self.print_summary()

def main():
    parser = argparse.ArgumentParser(
        description='Pre-flight validation for Portfolio CMS v5.3 database remediation'
    )
    parser.add_argument(
        '--project-root',
        type=str,
        default=None,
        help='Project root directory (default: current directory)'
    )
    parser.add_argument(
        '--check',
        type=str,
        choices=['all', 'code', 'migrations', 'config', 'docs'],
        default='all',
        help='Which checks to run'
    )
    
    args = parser.parse_args()
    
    validator = PreflightValidator(project_root=args.project_root)
    success = validator.run_all_checks(
        specific_check=None if args.check == 'all' else args.check
    )
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
