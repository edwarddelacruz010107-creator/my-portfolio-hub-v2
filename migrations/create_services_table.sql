-- =============================================================================
-- Migration: Create services table
-- Portfolio CMS v3.8
-- Run once against your database. Safe to re-run (uses IF NOT EXISTS).
-- =============================================================================

CREATE TABLE IF NOT EXISTS services (
    id            INT          NOT NULL AUTO_INCREMENT,
    tenant_slug   VARCHAR(120) NOT NULL DEFAULT 'default',
    title         VARCHAR(100) NOT NULL,
    description   TEXT,
    icon          VARCHAR(100) DEFAULT 'lucide:briefcase',
    -- Newline-separated feature bullet points
    features      TEXT,
    display_order INT          NOT NULL DEFAULT 0,
    is_visible    TINYINT(1)   NOT NULL DEFAULT 1,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    INDEX ix_services_tenant_slug  (tenant_slug),
    INDEX ix_services_tenant_order (tenant_slug, display_order),
    INDEX ix_services_tenant_visible (tenant_slug, is_visible)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- =============================================================================
-- Optional: Seed the default tenant with the 4 previously hard-coded services
-- so the portfolio looks unchanged immediately after migration.
-- Remove or adjust before running if you prefer to start with a blank slate.
-- =============================================================================

INSERT IGNORE INTO services (tenant_slug, title, description, icon, features, display_order, is_visible)
VALUES
  ('default', 'Web Development',
   'Building fast, responsive, and scalable websites using modern frameworks like React, Next.js, and vanilla JavaScript.',
   'lucide:code-2',
   'Single Page Apps\nProgressive Web Apps\nAPI Integration',
   0, 1),

  ('default', 'UI/UX Design',
   'Creating intuitive and visually appealing interfaces with a focus on user experience, accessibility, and modern design trends.',
   'lucide:palette',
   'Wireframing & Prototyping\nDesign Systems\nUser Research',
   1, 1),

  ('default', 'Responsive Design',
   'Ensuring every project looks perfect and functions flawlessly across all devices — from mobile phones to large desktops.',
   'lucide:smartphone',
   'Mobile-First Approach\nCross-Browser Testing\nFluid Layouts',
   2, 1),

  ('default', 'Performance Optimization',
   'Maximizing website speed and efficiency through code optimization, lazy loading, caching strategies, and best practices.',
   'lucide:zap',
   'Core Web Vitals\nSEO Optimization\nAccessibility Audit',
   3, 1);
