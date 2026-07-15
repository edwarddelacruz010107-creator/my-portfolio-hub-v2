"""Phase 0C source-level regression guards runnable without Flask packages."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


class Phase0CRoutingReadinessContentTests(unittest.TestCase):
    def test_route_contract_snapshot(self):
        source = read('app/services/public_route_contract.py')
        for route in (
            "'/administrator-portfolio'",
            "'/administrator-portfolio/project/<slug>'",
            "'/<tenant_slug>/'",
            "'/<tenant_slug>/project/<slug>'",
            "'/<tenant_slug>/contact'",
            "'tenant_subdomain'",
            "'custom_domain'",
        ):
            self.assertIn(route, source)
        self.assertIn("host.endswith(f'.{base}')", source)
        self.assertNotIn("len(parts) < 2", source)

    def test_contact_routes_delegate_to_one_service(self):
        for path in (
            'app/public/routes.py',
            'app/tenant/__init__.py',
            'app/main/routes/contact.py',
            'app/services/custom_domain_public.py',
        ):
            source = read(path)
            self.assertIn('communication.contact_service', source, path)
        self.assertIn("@limiter.limit('5 per hour')", read('app/public/routes.py'))
        self.assertIn("@limiter.limit('5 per 15 minutes')", read('app/main/routes/contact.py'))
        self.assertIn('5 per minute; 20 per hour', read('app/tenant/__init__.py'))

    def test_liveness_and_readiness_are_split(self):
        heartbeat = read('app/heartbeat/__init__.py')
        self.assertIn("@heartbeat_bp.route('/livez'", heartbeat)
        self.assertIn("@heartbeat_bp.route('/readyz'", heartbeat)
        live_body = heartbeat.split('def livez():', 1)[1].split("@heartbeat_bp.route('/readyz'", 1)[0]
        self.assertNotIn('SELECT 1', live_body)
        self.assertIn("('core_database'", heartbeat)
        self.assertIn("('tenant_database'", heartbeat)
        self.assertIn("checks['cache']", heartbeat)
        self.assertIn('READINESS_REQUIRE_REDIS = True', read('config.py'))
        self.assertIn('healthCheckPath: /readyz', read('render.yaml'))
        self.assertIn('/readyz', read('Dockerfile'))

    def test_fresh_account_has_workflow_but_no_fake_portfolio_rows(self):
        onboarding = read('app/services/tenant/onboarding_service.py')
        self.assertIn('OnboardingWorkflow(', onboarding)
        self.assertIn("title=''", onboarding)
        self.assertIn("bio=''", onboarding)
        for constructor in ('Project(', 'Skill(', 'Service(', 'Testimonial(', 'Certificate('):
            self.assertNotIn(constructor, onboarding)
        self.assertIn('ensure_onboarding_workspace', read('app/services/auth/complete_signup_service.py'))
        self.assertIn('onboarding-checklist', read('app/templates/admin/dashboard.html'))

    def test_unproven_landing_claims_are_absent(self):
        landing = read('app/public/templates/public/index.html')
        for false_claim in (
            'NORTHPEAK', 'VELORA', 'Sofia Álvarez', 'Ryan Okoye',
            'Hana Kobayashi', 'Tomasz Nowak', 'i.pravatar.cc',
        ):
            self.assertNotIn(false_claim, landing)

    def test_developer_pro_is_csp_local_and_real_data_only(self):
        theme = read('themes/developer_pro/templates/index.html')
        for forbidden in (
            'cdn.tailwindcss.com', 'cdnjs.cloudflare.com', 'fonts.googleapis.com',
            'picsum.photos', 'Create a simple text file as placeholder',
            'Ryzen 9 7950X', 'Uptime: 99.97%', 'JIAN CODY',
        ):
            self.assertNotIn(forbidden, theme)
        self.assertIn('No projects have been published yet.', theme)
        self.assertIn('No skills have been published yet.', theme)
        self.assertIn('{% if portfolio.resume_url %}', theme)
        self.assertIn('{% if portfolio.avatar_url %}', theme)

    def test_support_contract_is_explicit(self):
        support = read('SUPPORT_MATRIX.md')
        for required in ('CPython 3.12', 'PostgreSQL 16', 'Redis 7', 'PayMongo', 'Dodo Payments', 'Safari'):
            self.assertIn(required, support)


if __name__ == '__main__':
    unittest.main()
