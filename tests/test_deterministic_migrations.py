"""Phase 0B regression guards that do not require a live database.

The deployment rehearsal commands in PHASE_0B_DATABASE_MIGRATIONS.md remain
the authoritative PostgreSQL checks.  These tests cheaply prevent the unsafe
bootstrap and dual-bind wiring from regressing in ordinary CI.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TENANT_MODEL_PATH = ROOT / "app" / "models" / "tenant_data.py"
TENANT_BASELINE_PATH = (
    ROOT / "migrations" / "tenant" / "versions" / "0001_tenant_schema_baseline.py"
)
TENANT_CLASSES = {
    "Profile",
    "Skill",
    "Project",
    "ProjectReaction",
    "Testimonial",
    "Service",
    "Certificate",
    "WorkExperience",
}


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError):
        return None


def _migration_graph(versions_dir: Path):
    revisions: dict[str, tuple[str, ...]] = {}
    for path in versions_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = None
        parents = None
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if not isinstance(target, ast.Name):
                continue
            value = _literal(node.value)
            if target.id == "revision":
                revision = value
            elif target.id == "down_revision":
                parents = value
        if revision:
            if parents is None:
                parent_tuple = ()
            elif isinstance(parents, tuple):
                parent_tuple = parents
            else:
                parent_tuple = (parents,)
            revisions[revision] = parent_tuple
    return revisions


def _tenant_model_contract():
    tree = ast.parse(TENANT_MODEL_PATH.read_text(encoding="utf-8"))
    tables: dict[str, set[str]] = {}
    indexes: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in TENANT_CLASSES:
            continue
        table_name = None
        columns: set[str] = set()
        explicit_indexes: set[str] = set()
        indexed_columns: set[str] = set()
        for statement in node.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                if isinstance(target, ast.Name) and target.id == "__tablename__":
                    table_name = _literal(statement.value)
                elif isinstance(target, ast.Name) and isinstance(statement.value, ast.Call):
                    func = statement.value.func
                    if isinstance(func, ast.Attribute) and func.attr == "Column":
                        columns.add(target.id)
                        if any(
                            kw.arg == "index" and _literal(kw.value) is True
                            for kw in statement.value.keywords
                        ):
                            indexed_columns.add(target.id)
                if isinstance(target, ast.Name) and target.id == "__table_args__":
                    value = statement.value
                    if isinstance(value, (ast.Tuple, ast.List)):
                        for item in value.elts:
                            if (
                                isinstance(item, ast.Call)
                                and isinstance(item.func, ast.Attribute)
                                and item.func.attr == "Index"
                                and item.args
                            ):
                                name = _literal(item.args[0])
                                if name:
                                    explicit_indexes.add(name)
        assert table_name, f"No __tablename__ found for {node.name}"
        tables[table_name] = columns
        indexes.update(explicit_indexes)
        indexes.update(f"ix_{table_name}_{column}" for column in indexed_columns)
    return tables, indexes


def _tenant_baseline_contract():
    tree = ast.parse(TENANT_BASELINE_PATH.read_text(encoding="utf-8"))
    tables: dict[str, set[str]] = {}
    indexes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "Table"):
            continue
        table_name = _literal(node.args[0]) if node.args else None
        if not table_name:
            continue
        columns = set()
        for item in node.args[2:]:
            if (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and item.func.attr == "Column"
                and item.args
            ):
                name = _literal(item.args[0])
                if name:
                    columns.add(name)
        tables[table_name] = columns

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "INDEXES" for target in node.targets):
            continue
        value = _literal(node.value)
        indexes = {row[0] for row in value}

    # Later additive tenant revisions extend the baseline. The contract must
    # compare models with the current migration head, not revision 0001 alone.
    for path in sorted(TENANT_BASELINE_PATH.parent.glob("*.py")):
        if path == TENANT_BASELINE_PATH:
            continue
        revision_tree = ast.parse(path.read_text(encoding="utf-8"))
        upgrade = next(
            (
                node for node in revision_tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
            ),
            None,
        )
        if upgrade is None:
            continue
        for call in ast.walk(upgrade):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            name = _literal(call.args[0]) if call.args else None
            if call.func.attr == "create_index" and name:
                indexes.add(name)
            elif call.func.attr == "drop_index" and name:
                indexes.discard(name)
    return tables, indexes


class DeterministicMigrationTests(unittest.TestCase):
    def test_core_and_tenant_histories_each_have_one_base_and_head(self):
        for versions_dir in (
            ROOT / "migrations" / "versions",
            ROOT / "migrations" / "tenant" / "versions",
        ):
            graph = _migration_graph(versions_dir)
            self.assertTrue(graph, f"No revisions in {versions_dir}")
            bases = [revision for revision, parents in graph.items() if not parents]
            referenced = {parent for parents in graph.values() for parent in parents}
            heads = set(graph) - referenced
            missing = referenced - set(graph)
            self.assertEqual(len(bases), 1, (versions_dir, bases))
            self.assertEqual(len(heads), 1, (versions_dir, heads))
            self.assertEqual(missing, set(), (versions_dir, missing))

    def test_tenant_baseline_matches_model_table_column_and_index_contract(self):
        model_tables, model_indexes = _tenant_model_contract()
        migration_tables, migration_indexes = _tenant_baseline_contract()
        self.assertEqual(migration_tables, model_tables)
        self.assertEqual(migration_indexes, model_indexes)

    def test_tenant_environment_has_independent_version_table(self):
        source = (ROOT / "migrations" / "tenant" / "env.py").read_text(encoding="utf-8")
        self.assertIn("version_table='alembic_version_tenant'", source)
        self.assertIn("WorkExperience", source)

    def test_deployment_has_no_create_all_or_stamp_fallback(self):
        render = (ROOT / "render.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("flask db-upgrade-all", render)
        self.assertIn('- key: RUN_MIGRATIONS\n        value: "false"', render)
        self.assertIn("type: keyvalue", render)
        self.assertNotIn("type: redis", render)
        self.assertIn("ipAllowList: []", render)
        self.assertIn("plan: standard", render)
        self.assertNotIn("flask bootstrap-production-db", render)
        self.assertNotIn("flask db upgrade ||", render)
        self.assertIn("flask db-upgrade-all", dockerfile)
        self.assertNotIn("flask bootstrap-production-db", dockerfile)
        self.assertNotIn("ALLOW_CREATE_ALL_BOOTSTRAP_ON_MIGRATION_FAILURE:-true", dockerfile)

    def test_startup_tenant_validator_is_read_only(self):
        path = ROOT / "app" / "startup_validation.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "ensure_tenant_schema"
        )
        calls = {
            node.func.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(calls & {"create", "create_all", "execute"}, calls)

    def test_core_alembic_env_does_not_construct_flask_app(self):
        source = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
        self.assertNotIn("create_app(", source)
        self.assertIn("_TENANT_TABLE_NAMES", source)


if __name__ == "__main__":
    unittest.main()
