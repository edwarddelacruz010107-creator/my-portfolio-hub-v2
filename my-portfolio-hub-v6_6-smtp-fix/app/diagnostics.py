"""
Application startup diagnostics.

Runs automated checks at application startup to detect schema issues,
index duplications, and other common deployment problems.

Usage:
    from app.diagnostics import run_startup_diagnostics
    results = run_startup_diagnostics()
"""

import logging
from sqlalchemy import inspect, text
from app import db


logger = logging.getLogger(__name__)


class DiagnosticCheck:
    """Base class for diagnostic checks."""
    
    def __init__(self, name, description):
        self.name = name
        self.description = description
        self.passed = False
        self.issues = []
    
    def run(self):
        """Run the diagnostic check."""
        raise NotImplementedError
    
    def report(self):
        """Return formatted report."""
        status = "✓" if self.passed else "✗"
        return {
            'name': self.name,
            'description': self.description,
            'status': 'passed' if self.passed else 'failed',
            'issues': self.issues
        }


class DatabaseConnectionCheck(DiagnosticCheck):
    """Verify database is accessible."""
    
    def __init__(self):
        super().__init__('database_connection', 'Database connectivity')
    
    def run(self):
        try:
            db.session.execute(text("SELECT 1"))
            self.passed = True
            logger.info("✓ Database connection: OK")
        except Exception as e:
            self.passed = False
            self.issues = [str(e)]
            logger.error(f"✗ Database connection: {e}")


class SchemaConsistencyCheck(DiagnosticCheck):
    """Check model/database schema consistency."""
    
    def __init__(self):
        super().__init__('schema_consistency', 'Model/database schema consistency')
    
    def run(self):
        try:
            inspector = inspect(db.engine)
            db_tables = set(inspector.get_table_names())
            
            for table in db.metadata.tables.values():
                # Skip tenant-bound tables
                if getattr(table, 'bind_key', None) == 'tenant':
                    continue
                
                if table.name not in db_tables:
                    self.issues.append(f"Missing table: {table.name}")
            
            self.passed = len(self.issues) == 0
            
            if self.passed:
                logger.info(f"✓ Schema consistency: OK ({len(db.metadata.tables)} tables)")
            else:
                logger.warning(f"⚠ Schema consistency: {len(self.issues)} issues")
                for issue in self.issues:
                    logger.warning(f"  - {issue}")
        
        except Exception as e:
            self.passed = False
            self.issues = [str(e)]
            logger.error(f"✗ Schema consistency check failed: {e}")


class IndexDuplicationCheck(DiagnosticCheck):
    """Detect duplicate index definitions."""
    
    def __init__(self):
        super().__init__('index_duplication', 'Duplicate index detection')
    
    def run(self):
        try:
            duplicates = self._find_duplicates()
            
            if duplicates:
                self.passed = False
                for table_name, indexes in duplicates.items():
                    msg = f"Table '{table_name}' has duplicate indexes: {', '.join(indexes)}"
                    self.issues.append(msg)
                    logger.error(f"✗ {msg}")
            else:
                self.passed = True
                logger.info("✓ No duplicate indexes detected")
        
        except Exception as e:
            self.passed = False
            self.issues = [str(e)]
            logger.error(f"✗ Index duplication check failed: {e}")
    
    def _find_duplicates(self):
        """Find tables with both index=True and explicit indexes."""
        duplicates = {}
        
        for table in db.metadata.tables.values():
            # Skip tenant-bound tables
            if getattr(table, 'bind_key', None) == 'tenant':
                continue
            
            # Find columns with index=True
            auto_indexes = set()
            for col in table.columns:
                if col.index and col.name:
                    auto_index_name = f'ix_{table.name}_{col.name}'
                    auto_indexes.add(auto_index_name)
            
            # Find explicit indexes
            explicit_indexes = {idx.name for idx in table.indexes}
            
            # Check for overlaps
            overlaps = auto_indexes & explicit_indexes
            if overlaps:
                duplicates[table.name] = list(overlaps)
        
        return duplicates


class ColumnIndexConsistencyCheck(DiagnosticCheck):
    """Check that indexed columns are actually indexed in database."""
    
    def __init__(self):
        super().__init__('column_index_consistency', 'Column indexing consistency')
    
    def run(self):
        try:
            inspector = inspect(db.engine)
            issues_found = []
            
            for table in db.metadata.tables.values():
                # Skip tenant-bound tables
                if getattr(table, 'bind_key', None) == 'tenant':
                    continue
                
                if table.name not in inspector.get_table_names():
                    continue
                
                db_indexes = {idx['name'] for idx in inspector.get_indexes(table.name)}
                
                for index in table.indexes:
                    if index.name not in db_indexes:
                        msg = f"Index '{index.name}' defined in model but missing from database"
                        issues_found.append(msg)
                        logger.warning(f"⚠ {msg}")
            
            self.passed = len(issues_found) == 0
            self.issues = issues_found
            
            if self.passed:
                logger.info("✓ Column index consistency: OK")
        
        except Exception as e:
            self.passed = False
            self.issues = [str(e)]
            logger.error(f"✗ Column index consistency check failed: {e}")


class TablesExistenceCheck(DiagnosticCheck):
    """Verify all required tables exist."""
    
    def __init__(self):
        super().__init__('tables_existence', 'Required tables existence')
    
    def run(self):
        try:
            inspector = inspect(db.engine)
            db_tables = set(inspector.get_table_names())
            
            required_tables = [
                'tenants', 'users', 'subscriptions', 
                'inquiries', 'webhook_events',
                'payment_methods', 'payment_instructions',
                'payment_submissions', 'tenant_communication_settings', 'otp'
            ]
            
            missing = [t for t in required_tables if t not in db_tables]
            self.passed = len(missing) == 0
            
            if self.passed:
                logger.info(f"✓ All required tables exist ({len(required_tables)} tables)")
            else:
                self.issues = [f"Missing table: {t}" for t in missing]
                logger.error(f"✗ Missing {len(missing)} required tables")
                for issue in self.issues:
                    logger.error(f"  - {issue}")
        
        except Exception as e:
            self.passed = False
            self.issues = [str(e)]
            logger.error(f"✗ Tables existence check failed: {e}")


def run_startup_diagnostics():
    """
    Run all diagnostic checks.
    
    Returns:
        dict: Results for each check
    """
    logger.info("=" * 80)
    logger.info("STARTUP DIAGNOSTICS")
    logger.info("=" * 80)
    
    checks = [
        DatabaseConnectionCheck(),
        SchemaConsistencyCheck(),
        TablesExistenceCheck(),
        IndexDuplicationCheck(),
        ColumnIndexConsistencyCheck(),
    ]
    
    results = {}
    for check in checks:
        try:
            check.run()
            results[check.name] = check.report()
        except Exception as e:
            logger.error(f"Unexpected error in {check.name}: {e}")
            results[check.name] = {
                'name': check.name,
                'status': 'error',
                'issues': [str(e)]
            }
    
    # Summary
    passed_count = sum(1 for r in results.values() if r['status'] == 'passed')
    total_count = len(results)
    
    logger.info("=" * 80)
    logger.info(f"DIAGNOSTICS SUMMARY: {passed_count}/{total_count} checks passed")
    logger.info("=" * 80)
    
    return results


def print_diagnostic_report(results):
    """Print formatted diagnostic report."""
    print("\n" + "=" * 80)
    print("DIAGNOSTIC REPORT")
    print("=" * 80)
    
    for name, result in results.items():
        status_symbol = "✓" if result['status'] == 'passed' else "✗"
        print(f"\n{status_symbol} {result['name']}")
        print(f"  {result['description']}")
        
        if result['issues']:
            print(f"  Issues ({len(result['issues'])}):")
            for issue in result['issues']:
                print(f"    - {issue}")
    
    print("\n" + "=" * 80)


if __name__ == '__main__':
    from app import create_app
    app = create_app()
    
    with app.app_context():
        results = run_startup_diagnostics()
        print_diagnostic_report(results)