"""Pure deterministic portfolio-intelligence rubric.

The module deliberately has no Flask, database, network, or filesystem
dependency.  Runtime code supplies stored portfolio facts; this module turns
those facts into explainable evidence and recommendations.  A point is never
awarded without a fact, and checks that require rendered or external evidence
are reported as ``not_evaluated`` instead of guessed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence


RUBRIC_VERSION = "portfolio-intelligence-2026.07-v1"
SUPPORTED_THEME_IDS = (
    "default",
    "developer_pro",
    "blockform_brutal",
    "schematic_spec",
    "developer_journal",
)
VALID_STATUSES = frozenset({"pass", "fail", "not_evaluated"})
ACTION_ROUTE_ALLOWLIST = frozenset({
    "admin.edit_profile",
    "admin.projects",
    "admin.services",
    "admin.testimonials",
    "admin.certificates",
    "admin.experiences",
    "admin.seo_settings",
    "admin.settings",
})


@dataclass(frozen=True)
class PortfolioFacts:
    """Canonical facts read from one tenant's stored portfolio state."""

    theme_id: str = "default"
    profile: Mapping[str, Any] = field(default_factory=dict)
    projects: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    services: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    testimonials: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    certificates: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    experiences: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    contact: Mapping[str, Any] = field(default_factory=dict)
    latest_content_at: str | datetime | date | None = None
    as_of: date | datetime | str | None = None


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    status: str
    points: int
    earned: int
    fact: Any
    explanation: str
    action_route: str | None
    impact: str = "medium"
    effort: str = "medium"
    evidence_type: str = "stored_fact"


@dataclass(frozen=True)
class DimensionDefinition:
    key: str
    label: str
    weight: int
    action_route: str


DIMENSIONS = (
    DimensionDefinition("profile", "Profile", 15, "admin.edit_profile"),
    DimensionDefinition("projects", "Projects", 15, "admin.projects"),
    DimensionDefinition("services", "Services", 8, "admin.services"),
    DimensionDefinition("testimonials", "Testimonials", 8, "admin.testimonials"),
    DimensionDefinition("certificates", "Certificates", 7, "admin.certificates"),
    DimensionDefinition("experience", "Experience", 8, "admin.experiences"),
    DimensionDefinition("seo", "SEO metadata", 14, "admin.seo_settings"),
    DimensionDefinition("accessibility", "Accessibility fields", 10, "admin.edit_profile"),
    DimensionDefinition("contact", "Contact readiness", 8, "admin.settings"),
    DimensionDefinition("freshness", "Freshness", 7, "admin.edit_profile"),
)
assert sum(item.weight for item in DIMENSIONS) == 100


def _text(value: Any) -> str:
    return str(value or "").strip()


def _present(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(_text(value))


def _visible(items: Sequence[Mapping[str, Any]], *, project: bool = False) -> list[Mapping[str, Any]]:
    if project:
        return [item for item in items if _text(item.get("status")).lower() == "published"]
    return [item for item in items if item.get("is_visible", True) is True]


def _check(
    key: str,
    label: str,
    passed: bool,
    points: int,
    fact: Any,
    explanation: str,
    route: str,
    *,
    impact: str = "medium",
    effort: str = "medium",
) -> Check:
    return Check(
        key=key,
        label=label,
        status="pass" if passed else "fail",
        points=points,
        earned=points if passed else 0,
        fact=fact,
        explanation=explanation,
        action_route=route,
        impact=impact,
        effort=effort,
    )


def _unavailable(key: str, label: str, explanation: str, fact: Any = None) -> Check:
    return Check(
        key=key,
        label=label,
        status="not_evaluated",
        points=0,
        earned=0,
        fact=fact,
        explanation=explanation,
        action_route=None,
        evidence_type="unavailable",
    )


def _profile_checks(facts: PortfolioFacts) -> list[Check]:
    p = facts.profile
    return [
        _check("profile.identity", "Name and professional title", _present(p.get("name")) and _present(p.get("title")), 25,
               {"name": _text(p.get("name")), "title": _text(p.get("title"))},
               "Add both a public name and professional title.", "admin.edit_profile", impact="high", effort="low"),
        _check("profile.summary", "Portfolio biography", len(_text(p.get("bio"))) >= 80, 25,
               {"bio_characters": len(_text(p.get("bio")))},
               "Add a biography of at least 80 characters that explains your work.", "admin.edit_profile", impact="high"),
        _check("profile.location", "Location", _present(p.get("location")), 15,
               {"location_present": _present(p.get("location"))},
               "Add the location or service area you want visitors to see.", "admin.edit_profile", effort="low"),
        _check("profile.email", "Public contact email", _present(p.get("email")), 20,
               {"email_present": _present(p.get("email"))},
               "Add a public contact email.", "admin.edit_profile", impact="high", effort="low"),
        _check("profile.image", "Profile image", _present(p.get("profile_image")), 15,
               {"profile_image_present": _present(p.get("profile_image"))},
               "Upload a profile image.", "admin.edit_profile", effort="low"),
    ]


def _project_checks(facts: PortfolioFacts) -> list[Check]:
    rows = _visible(facts.projects, project=True)
    count = len(rows)
    detailed = sum(bool(_text(row.get("description")) or _text(row.get("description_short"))) for row in rows)
    outcomes = sum(bool(_text(row.get("outcome_summary")) or _text(row.get("client_quote"))) for row in rows)
    linked = sum(bool(_text(row.get("live_url")) or _text(row.get("github_url")) or _text(row.get("prototype_url"))) for row in rows)
    return [
        _check("projects.published", "Published project", count >= 1, 25, {"published_count": count},
               "Publish at least one real project.", "admin.projects", impact="high"),
        _check("projects.depth", "Portfolio depth", count >= 3, 20, {"published_count": count},
               "Publish at least three projects to demonstrate breadth.", "admin.projects", impact="high"),
        _check("projects.descriptions", "Project descriptions", count > 0 and detailed == count, 20,
               {"published_count": count, "described_count": detailed},
               "Add a description to every published project.", "admin.projects"),
        _check("projects.outcomes", "Outcomes or client evidence", count > 0 and outcomes == count, 20,
               {"published_count": count, "evidence_count": outcomes},
               "Document an outcome or client quote for every published project.", "admin.projects", impact="high"),
        _check("projects.links", "Working destination fields", count > 0 and linked == count, 15,
               {"published_count": count, "linked_count": linked},
               "Add a live, repository, or prototype URL to every published project.", "admin.projects"),
    ]


def _service_checks(facts: PortfolioFacts) -> list[Check]:
    rows = _visible(facts.services)
    described = sum(bool(_text(row.get("description"))) for row in rows)
    featured = sum(bool(_text(row.get("features"))) for row in rows)
    return [
        _check("services.visible", "Visible service", len(rows) >= 1, 40, {"visible_count": len(rows)},
               "Publish at least one service.", "admin.services", impact="high", effort="low"),
        _check("services.descriptions", "Service descriptions", bool(rows) and described == len(rows), 35,
               {"visible_count": len(rows), "described_count": described},
               "Describe every visible service.", "admin.services"),
        _check("services.features", "Service deliverables", bool(rows) and featured == len(rows), 25,
               {"visible_count": len(rows), "features_count": featured},
               "List features or deliverables for every visible service.", "admin.services"),
    ]


def _testimonial_checks(facts: PortfolioFacts) -> list[Check]:
    rows = _visible(facts.testimonials)
    detailed = sum(bool(_text(row.get("author_name")) and len(_text(row.get("content"))) >= 30) for row in rows)
    return [
        _check("testimonials.visible", "Visible testimonial", len(rows) >= 1, 55, {"visible_count": len(rows)},
               "Publish at least one authentic testimonial.", "admin.testimonials", impact="high"),
        _check("testimonials.attributed", "Attributed testimonial details", bool(rows) and detailed == len(rows), 45,
               {"visible_count": len(rows), "attributed_count": detailed},
               "Add an author and substantive quote to every visible testimonial.", "admin.testimonials"),
    ]


def _certificate_checks(facts: PortfolioFacts) -> list[Check]:
    rows = _visible(facts.certificates)
    verified = sum(bool(_text(row.get("credential_id")) or _text(row.get("verification_url"))) for row in rows)
    detailed = sum(bool(_text(row.get("description")) or _text(row.get("skills"))) for row in rows)
    return [
        _check("certificates.visible", "Visible certificate", len(rows) >= 1, 45, {"visible_count": len(rows)},
               "Publish a relevant certificate or badge.", "admin.certificates"),
        _check("certificates.verification", "Credential evidence", bool(rows) and verified == len(rows), 35,
               {"visible_count": len(rows), "verified_count": verified},
               "Add a credential ID or verification URL to every visible certificate.", "admin.certificates", impact="high"),
        _check("certificates.context", "Certificate context", bool(rows) and detailed == len(rows), 20,
               {"visible_count": len(rows), "context_count": detailed},
               "Describe the relevance or skills for every visible certificate.", "admin.certificates", effort="low"),
    ]


def _experience_checks(facts: PortfolioFacts) -> list[Check]:
    rows = _visible(facts.experiences)
    dated = sum(bool(row.get("start_date")) for row in rows)
    detailed = sum(bool(_text(row.get("description")) or _text(row.get("achievements"))) for row in rows)
    return [
        _check("experience.visible", "Visible experience", len(rows) >= 1, 40, {"visible_count": len(rows)},
               "Publish at least one relevant experience entry.", "admin.experiences", impact="high"),
        _check("experience.dates", "Experience dates", bool(rows) and dated == len(rows), 25,
               {"visible_count": len(rows), "dated_count": dated},
               "Add a start date to every visible experience entry.", "admin.experiences"),
        _check("experience.evidence", "Responsibilities or achievements", bool(rows) and detailed == len(rows), 35,
               {"visible_count": len(rows), "detailed_count": detailed},
               "Document responsibilities or achievements for every visible experience entry.", "admin.experiences", impact="high"),
    ]


def _seo_checks(facts: PortfolioFacts) -> list[Check]:
    p = facts.profile
    title_len = len(_text(p.get("meta_title")))
    description_len = len(_text(p.get("meta_description")))
    return [
        _check("seo.title", "SEO title", 30 <= title_len <= 60, 30, {"characters": title_len},
               "Write an SEO title between 30 and 60 characters.", "admin.seo_settings", impact="high", effort="low"),
        _check("seo.description", "SEO description", 120 <= description_len <= 160, 30, {"characters": description_len},
               "Write an SEO description between 120 and 160 characters.", "admin.seo_settings", impact="high"),
        _check("seo.social_image", "Social sharing image", _present(p.get("og_image")), 25,
               {"social_image_present": _present(p.get("og_image"))},
               "Upload a social sharing image.", "admin.seo_settings"),
        _check("seo.indexability", "Indexability setting", p.get("seo_indexable") is True, 15,
               {"seo_indexable": bool(p.get("seo_indexable"))},
               "Enable indexing if this portfolio should appear in search.", "admin.seo_settings", effort="low"),
    ]


def _accessibility_checks(facts: PortfolioFacts) -> list[Check]:
    p = facts.profile
    image_pairs: list[tuple[str, str, str]] = []
    if _present(p.get("profile_image")):
        image_pairs.append(("profile", _text(p.get("profile_image")), _text(p.get("profile_image_alt"))))
    for row in _visible(facts.projects, project=True):
        for field_name, alt_name in (("image", "image_alt"), ("before_image", "before_image_alt"), ("after_image", "after_image_alt")):
            if _present(row.get(field_name)):
                image_pairs.append((f"project:{row.get('id', '')}:{field_name}", _text(row.get(field_name)), _text(row.get(alt_name))))

    checks: list[Check] = []
    if image_pairs:
        missing = [name for name, _path, alt in image_pairs if not alt]
        checks.append(_check("accessibility.image_alt", "Alternative text fields", not missing, 100,
                             {"image_count": len(image_pairs), "missing_alt": missing},
                             "Add alternative text for every stored portfolio image.",
                             "admin.edit_profile" if missing == ["profile"] else "admin.projects", impact="high", effort="low"))
    else:
        checks.append(_unavailable("accessibility.image_alt", "Alternative text fields",
                                   "No stored portfolio images require alternative text.", {"image_count": 0}))
    checks.extend([
        _unavailable("accessibility.headings", "Rendered heading order",
                     "Requires a rendered-theme contract scan; no scan evidence is stored."),
        _unavailable("accessibility.link_labels", "Rendered link labels",
                     "Requires rendered markup; URL fields alone cannot prove accessible labels."),
        _unavailable("accessibility.contrast", "Rendered color contrast",
                     "Requires resolved theme colors and a deterministic contrast scan."),
    ])
    return checks


def _contact_checks(facts: PortfolioFacts) -> list[Check]:
    c = facts.contact
    internal = c.get("internal_inbox_available") is True
    email = _present(c.get("public_email"))
    checks = [
        _check("contact.internal_inbox", "Internal inquiry inbox", internal, 55,
               {"internal_inbox_available": internal},
               "Enable the internal inquiry inbox.", "admin.settings", impact="high"),
        _check("contact.reply_address", "Public reply address", email, 45,
               {"public_email_present": email},
               "Add a public email visitors can use for replies.", "admin.edit_profile", impact="high", effort="low"),
    ]
    provider = _text(c.get("provider")) or "disabled"
    if provider == "disabled":
        checks.append(_unavailable("contact.external_provider", "External delivery provider",
                                   "Optional external delivery is not configured; the internal inbox remains the canonical destination.",
                                   {"provider": provider}))
    else:
        checks.append(Check(
            key="contact.external_provider",
            label="External delivery provider",
            status="pass" if c.get("external_provider_ready") is True else "fail",
            points=0,
            earned=0,
            fact={"provider": provider, "configured": c.get("external_provider_ready") is True},
            explanation="Complete the selected external delivery provider configuration.",
            action_route="admin.settings",
            impact="medium",
            effort="medium",
        ))
    return checks


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _as_of_date(value: Any) -> date:
    parsed = _parse_date(value)
    return parsed or datetime.now(timezone.utc).date()


def _freshness_checks(facts: PortfolioFacts) -> list[Check]:
    latest = _parse_date(facts.latest_content_at)
    if latest is None:
        return [_unavailable("freshness.latest_update", "Recent content update",
                             "No stored content timestamp is available.")]
    age_days = max(0, (_as_of_date(facts.as_of) - latest).days)
    return [_check("freshness.latest_update", "Recent content update", age_days <= 90, 100,
                   {"latest_content_date": latest.isoformat(), "age_days": age_days},
                   "Review and update portfolio content that is more than 90 days old.",
                   "admin.edit_profile", impact="medium", effort="medium")]


CHECK_BUILDERS = {
    "profile": _profile_checks,
    "projects": _project_checks,
    "services": _service_checks,
    "testimonials": _testimonial_checks,
    "certificates": _certificate_checks,
    "experience": _experience_checks,
    "seo": _seo_checks,
    "accessibility": _accessibility_checks,
    "contact": _contact_checks,
    "freshness": _freshness_checks,
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def portfolio_hash(facts: PortfolioFacts) -> str:
    payload = _jsonable(asdict(facts))
    latest = _parse_date(facts.latest_content_at)
    if latest is None:
        freshness_bucket = "unavailable"
    else:
        freshness_bucket = "fresh" if max(0, (_as_of_date(facts.as_of) - latest).days) <= 90 else "stale"
    # The date itself is an evaluation input, not portfolio content.  Hash the
    # only score-changing freshness state so snapshots remain cached until the
    # 90-day boundary instead of creating artificial daily history.
    payload.pop("as_of", None)
    payload["freshness_bucket"] = freshness_bucket
    payload["rubric_version"] = RUBRIC_VERSION
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_portfolio(facts: PortfolioFacts, *, calculated_at: datetime | None = None) -> dict[str, Any]:
    """Return a deterministic, JSON-serializable intelligence result."""
    dimensions: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    weighted_earned = 0.0
    evaluated_weight = 0

    for definition in DIMENSIONS:
        checks = CHECK_BUILDERS[definition.key](facts)
        if any(check.status not in VALID_STATUSES for check in checks):
            raise ValueError(f"invalid check status in {definition.key}")
        score_checks = [check for check in checks if check.points > 0]
        possible = sum(check.points for check in score_checks)
        earned = sum(check.earned for check in score_checks)
        evaluated = possible > 0
        score = round((earned / possible) * 100) if evaluated else None
        if evaluated:
            evaluated_weight += definition.weight
            weighted_earned += definition.weight * (score / 100)

        evidence = [_jsonable(asdict(check)) for check in checks]
        counts = {status: sum(check.status == status for check in checks) for status in VALID_STATUSES}
        dimensions.append({
            "key": definition.key,
            "label": definition.label,
            "weight": definition.weight,
            "score": score,
            "evaluated": evaluated,
            "status_counts": counts,
            "action_route": definition.action_route,
            "evidence": evidence,
        })
        for check in checks:
            if check.status == "fail" and check.points > 0:
                if check.action_route not in ACTION_ROUTE_ALLOWLIST:
                    raise ValueError(f"unapproved action route: {check.action_route}")
                recommendations.append({
                    "key": check.key,
                    "title": check.label,
                    "recommendation": check.explanation,
                    "action_route": check.action_route,
                    "impact": check.impact,
                    "effort": check.effort,
                    "dimension": definition.key,
                    "available_points": check.points,
                    "source_fact": _jsonable(check.fact),
                })

    impact_order = {"high": 0, "medium": 1, "low": 2}
    effort_order = {"low": 0, "medium": 1, "high": 2}
    recommendations.sort(key=lambda item: (
        impact_order.get(item["impact"], 9),
        effort_order.get(item["effort"], 9),
        -int(item["available_points"]),
        item["key"],
    ))

    total = round((weighted_earned / evaluated_weight) * 100) if evaluated_weight else None
    calculated = calculated_at or datetime.now(timezone.utc)
    if calculated.tzinfo is None:
        calculated = calculated.replace(tzinfo=timezone.utc)
    return {
        "rubric_version": RUBRIC_VERSION,
        "portfolio_hash": portfolio_hash(facts),
        "theme_id": facts.theme_id if facts.theme_id in SUPPORTED_THEME_IDS else "default",
        "total_score": total,
        "evaluated_weight": evaluated_weight,
        "dimensions": dimensions,
        "recommendations": recommendations,
        "calculated_at": calculated.isoformat(),
        "definition": "Weighted readiness from stored portfolio facts; unavailable checks do not affect the total.",
    }
