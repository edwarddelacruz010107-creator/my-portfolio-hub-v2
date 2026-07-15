# Design token deprecation registry

Status: expand/migrate/contract registry, 2026-07-14

The Phase 1 change adds the canonical API without removing selectors that existing pages may still depend on. Removal happens only after characterization coverage and a measured zero-usage release.

| Legacy owner | Current compatibility role | Migration owner/phase | Contract action |
|---|---|---|---|
| `main.css` | Tenant default-page aliases | Phase 2 public/tenant component migration | Core `--bg`, `--text`, and `--accent` values now resolve from `design-system.css`; remove its root after page snapshots pass. |
| `public-design-system.css` | `--ph-*` public platform namespace | Phase 2 public components | Namespace delegates to canonical semantic tokens; retain until all `ph-*` components are migrated. |
| `landing.css` | Standalone marketing-page palette/layout | Phase 2 public platform pages | Typography now uses Syne/DM Sans/JetBrains Mono; map palette/layout values per component before removing the root. |
| `style.css` | Legacy default tenant theme | Phase 7 theme contract | Typography now uses Syne/DM Sans; palette remains theme-owned until the manifest migration. |
| `admin.css`, `superadmin.css`, `superadmin-unify.css` | Historical component aliases and overrides | Phase 2 admin/superadmin migration | Characterize each page family, replace markup with shared components, then delete unused values. |
| `admin-v3-enhancements.css`, `style-v3-enhancements.css` | Release compatibility patches | Phase 2/7 | Trace selectors to templates; merge verified survivors and delete the patch file after one rollback release. |
| `typography-consistency.css` | `--ui-*` dashboard type aliases | Phase 2 shell migration | Aliases resolve to canonical families; remove when shared shell components own the scale. |
| Individual theme styles | Theme-specific visual tokens | Phase 7 marketplace/theme contract | Preserve brand expression; migrate shared behavior to the manifest contract, never globally replace theme colors. |
| `theme-contract.css` | Cross-theme behavioral fallback | Phase 7 | Keep its `--mph-*` behavior namespace until every supported manifest declares a compatible contract version. |

## Removal protocol

1. Capture route/template and visual characterization for the page family.
2. Move one component family to the canonical API.
3. Search runtime templates and generated CSS for the legacy selector/token.
4. Record zero measured usage through one release and retain a rollback build.
5. Remove the alias and its registry row in the contract release.

Global search-and-replace is prohibited because identical names can represent platform semantics in one file and deliberate theme expression in another.
