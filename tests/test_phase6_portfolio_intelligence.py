"""Phase 6 deterministic scoring, history, isolation, and UX contract."""
from __future__ import annotations

from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def _load_domain():
    path = ROOT / "app/services/intelligence/domain.py"
    spec = importlib.util.spec_from_file_location("phase6_intelligence_domain", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


domain = _load_domain()
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _complete(theme_id="default"):
    projects = tuple({
        "id": index,
        "status": "published",
        "title": f"Project {index}",
        "description": "A substantive project description.",
        "image": f"project-{index}.webp",
        "image_alt": f"Project {index} interface",
        "before_image": "",
        "before_image_alt": "",
        "after_image": "",
        "after_image_alt": "",
        "live_url": f"https://example.test/{index}",
        "outcome_summary": "Reduced completion time with measured results.",
    } for index in range(1, 4))
    return domain.PortfolioFacts(
        theme_id=theme_id,
        profile={
            "name": "Ada Example",
            "title": "Product Engineer",
            "bio": "I design and build dependable software products with clear outcomes for growing teams.",
            "location": "Manila",
            "email": "ada@example.test",
            "profile_image": "ada.webp",
            "profile_image_alt": "Ada Example at her workspace",
            "meta_title": "Ada Example — Product Engineer Portfolio",
            "meta_description": "Explore selected software products, case studies, measurable outcomes, and engineering services from Ada Example, a product engineer in Manila.",
            "og_image": "share.webp",
            "seo_indexable": True,
        },
        projects=projects,
        services=({"is_visible": True, "title": "Product engineering", "description": "End-to-end delivery.", "features": "Discovery\nImplementation"},),
        testimonials=({"is_visible": True, "author_name": "Verified Client", "content": "Ada delivered the agreed outcome on schedule and communicated every decision clearly."},),
        certificates=({"is_visible": True, "title": "Architecture", "description": "Advanced architecture program.", "credential_id": "CERT-123", "skills": "Architecture"},),
        experiences=({"is_visible": True, "role": "Lead Engineer", "company": "Example Co", "start_date": "2023-01-01", "description": "Led delivery.", "achievements": "Improved release reliability."},),
        contact={"internal_inbox_available": True, "public_email": "ada@example.test", "provider": "disabled", "external_provider_ready": False},
        latest_content_at="2026-07-01T00:00:00+00:00",
        as_of=date(2026, 7, 14),
    )


def _partial():
    return domain.PortfolioFacts(
        theme_id="default",
        profile={"name": "Ada", "title": "Engineer", "email": "ada@example.test", "seo_indexable": True},
        projects=({"id": 1, "status": "published", "title": "One", "description": "Short description"},),
        contact={"internal_inbox_available": True, "public_email": "ada@example.test", "provider": "disabled"},
        latest_content_at="2026-07-01T00:00:00+00:00",
        as_of=date(2026, 7, 14),
    )


def _evaluate(facts):
    return domain.evaluate_portfolio(facts, calculated_at=NOW)


def test_golden_empty_partial_complete_are_ordered():
    empty = _evaluate(domain.PortfolioFacts(as_of=date(2026, 7, 14)))
    partial = _evaluate(_partial())
    complete = _evaluate(_complete())
    assert 0 <= empty["total_score"] < partial["total_score"] < complete["total_score"] == 100
    assert empty["rubric_version"] == domain.RUBRIC_VERSION
    assert complete["evaluated_weight"] == 100


def test_all_supported_themes_share_the_same_content_score():
    scores = {_evaluate(_complete(theme))["total_score"] for theme in domain.SUPPORTED_THEME_IDS}
    assert scores == {100}


def test_complete_fixture_has_one_dimension_per_weighted_contract():
    result = _evaluate(_complete())
    assert [item["key"] for item in result["dimensions"]] == [item.key for item in domain.DIMENSIONS]
    assert sum(item["weight"] for item in result["dimensions"]) == 100
    assert all(item["score"] == 100 for item in result["dimensions"])


def test_additive_changes_are_monotonic_for_the_partial_fixture():
    partial = _partial()
    before = _evaluate(partial)["total_score"]
    improved_profile = dict(partial.profile)
    improved_profile.update({
        "bio": "I design and deliver reliable software systems with useful, measurable outcomes for clients.",
        "location": "Manila",
    })
    improved = domain.PortfolioFacts(**{**partial.__dict__, "profile": improved_profile})
    assert _evaluate(improved)["total_score"] >= before


def test_deleting_published_projects_removes_credit_and_changes_hash():
    complete = _complete()
    without_projects = domain.PortfolioFacts(**{**complete.__dict__, "projects": ()})
    before = _evaluate(complete)
    after = _evaluate(without_projects)
    assert after["total_score"] < before["total_score"]
    assert after["portfolio_hash"] != before["portfolio_hash"]
    projects = next(item for item in after["dimensions"] if item["key"] == "projects")
    assert projects["score"] == 0


def test_freshness_hash_changes_only_when_score_boundary_changes():
    facts = _complete()
    day_89 = domain.PortfolioFacts(**{**facts.__dict__, "latest_content_at": "2026-04-16", "as_of": date(2026, 7, 14)})
    day_90 = domain.PortfolioFacts(**{**day_89.__dict__, "as_of": date(2026, 7, 15)})
    day_91 = domain.PortfolioFacts(**{**day_89.__dict__, "as_of": date(2026, 7, 16)})
    a, b, c = _evaluate(day_89), _evaluate(day_90), _evaluate(day_91)
    assert a["portfolio_hash"] == b["portfolio_hash"]
    assert b["portfolio_hash"] != c["portfolio_hash"]
    assert b["total_score"] > c["total_score"]


def test_every_awarded_or_failed_point_has_stored_fact_evidence():
    result = _evaluate(_partial())
    scored = [check for dimension in result["dimensions"] for check in dimension["evidence"] if check["points"] > 0]
    assert scored
    assert all(check["evidence_type"] == "stored_fact" for check in scored)
    assert all(check["fact"] is not None for check in scored)
    failed_keys = {check["key"] for check in scored if check["status"] == "fail"}
    assert failed_keys == {item["key"] for item in result["recommendations"]}


def test_accessibility_never_guesses_rendered_checks():
    result = _evaluate(_complete())
    accessibility = next(item for item in result["dimensions"] if item["key"] == "accessibility")
    statuses = {item["key"]: item["status"] for item in accessibility["evidence"]}
    assert statuses["accessibility.image_alt"] == "pass"
    assert statuses["accessibility.headings"] == "not_evaluated"
    assert statuses["accessibility.link_labels"] == "not_evaluated"
    assert statuses["accessibility.contrast"] == "not_evaluated"


def test_recommendation_routes_are_allowlisted_and_prioritized():
    recommendations = _evaluate(_partial())["recommendations"]
    assert recommendations
    assert all(item["action_route"] in domain.ACTION_ROUTE_ALLOWLIST for item in recommendations)
    priority = {"high": 0, "medium": 1, "low": 2}
    observed = [priority[item["impact"]] for item in recommendations]
    assert observed == sorted(observed)


def test_calculation_is_deterministic_with_fixed_clock():
    first = _evaluate(_complete())
    second = _evaluate(_complete())
    assert first == second


def test_snapshot_migration_has_no_fabricated_history_and_is_append_only():
    migration = (ROOT / "migrations/versions/0060_portfolio_intelligence.py").read_text()
    model = (ROOT / "app/models/intelligence.py").read_text()
    assert 'down_revision = "0059"' in migration
    assert "portfolio_intelligence_snapshots" in migration
    assert "INSERT INTO" not in migration.upper()
    assert "BEFORE UPDATE OR DELETE" in migration
    assert "before_update" in model and "before_delete" in model


def test_snapshot_cache_is_tenant_version_hash_scoped():
    migration = (ROOT / "migrations/versions/0060_portfolio_intelligence.py").read_text()
    service = (ROOT / "app/services/intelligence/intelligence_service.py").read_text()
    assert "uq_portfolio_intelligence_tenant_hash_version" in migration
    assert 'tenant_id=tenant_id' in service
    assert 'portfolio_hash=calculated["portfolio_hash"]' in service
    assert "rubric_version=RUBRIC_VERSION" in service
    assert "except IntegrityError" in service


def test_runtime_collectors_filter_every_tenant_owned_query():
    service = (ROOT / "app/services/intelligence/intelligence_service.py").read_text()
    for model in ("Profile", "Project", "Service", "Testimonial", "Certificate", "WorkExperience", "TenantFormSettings"):
        assert f"{model}.query.filter_by(tenant_id=tenant_id)" in service


def test_authenticated_ui_has_exact_editors_and_evidence_boundaries():
    route = (ROOT / "app/admin/routes/intelligence.py").read_text()
    template = (ROOT / "app/templates/admin/intelligence.html").read_text()
    assert "@admin_required" in route
    assert "ACTION_ROUTE_ALLOWLIST" in route
    assert "Rendered evidence unavailable" in template
    assert "not evaluated" in template.lower()
    assert "external crawler" not in (route + template).lower()
    assert "url_for(endpoint) if endpoint in ACTION_ROUTE_ALLOWLIST" in route


def test_seo_screen_uses_canonical_dimension_not_a_duplicate_formula():
    route = (ROOT / "app/admin/routes/profile_appearance.py").read_text()
    template = (ROOT / "app/templates/admin/seo.html").read_text()
    assert "_portfolio_seo_readiness" in route
    assert "seo_readiness.status_counts.pass" in template
    assert "done*25" not in template
    assert "seo_completed * 25" not in template


def test_phase6_css_is_external_and_registered_for_token_lint():
    template = (ROOT / "app/templates/admin/intelligence.html").read_text()
    css = (ROOT / "app/static/css/portfolio-intelligence-v1.css").read_text()
    lint = (ROOT / "tools/lint_design_tokens.py").read_text()
    assert "<style" not in template and "style=" not in template
    assert "<script" not in template
    assert "portfolio-intelligence-v1.css" in template and "portfolio-intelligence-v1.css" in lint
    assert ":root" not in css
