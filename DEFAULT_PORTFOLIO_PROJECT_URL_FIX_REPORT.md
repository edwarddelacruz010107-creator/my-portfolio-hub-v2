# Default Portfolio Project Visibility and URL Fix

## What was fixed

1. **Default owner portfolio URL**
   - Added clean public URL: `/administrator-portfolio`
   - Added default project detail URL: `/administrator-portfolio/project/<slug>`
   - Legacy `/u/default`, `/default`, and `/default/` now redirect to `/administrator-portfolio`
   - `tenant_portfolio_url('default')` now generates `/administrator-portfolio`
   - `tenant_project_url('default', slug)` now generates `/administrator-portfolio/project/<slug>`

2. **Projects not showing on the default portfolio**
   - The Admin Projects page was listing records by `tenant_slug`, while public rendering used `tenant_id` only through `Project.published_for_tenant()`.
   - If the default/admin tenant had a stale or mismatched `tenant_id` after deployment/bootstrap, projects could appear in Admin but not public portfolio.
   - `Project.public_for_tenant()` now resolves public projects by `tenant_slug OR tenant_id` for string slugs.

3. **Existing featured draft projects**
   - The screenshot project is marked `draft`. Drafts normally do not appear publicly.
   - For the protected owner/default portfolio only, featured draft projects now render on `/administrator-portfolio` so existing showcase cards do not disappear.
   - Regular tenant portfolios still show published projects only.

4. **New project publishing behavior**
   - New projects default to `Published`, so users do not accidentally create invisible project cards.
   - Project create/edit/toggle/reorder now clears the cached public portfolio page.
   - Edit saves now re-sync `tenant_id` from the active tenant slug.

5. **Admin image preview**
   - Project edit image preview now uses the resilient `upload_url('projects')` helper instead of hardcoded `/static/uploads/projects/...`.

## New public paths

```text
/administrator-portfolio
/administrator-portfolio/project/<project-slug>
```

## Backward-compatible redirects

```text
/u/default  -> /administrator-portfolio
/default    -> /administrator-portfolio
/default/   -> /administrator-portfolio
```
