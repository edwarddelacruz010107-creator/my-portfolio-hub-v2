# Dodo checkout and toggle fix

- Added persistent Superadmin Activate/Deactivate control using PlatformSetting.
- Render environment remains the hard safety switch; secrets remain in Render.
- Fixed tenant checkout: the button now sends a CSRF-protected POST instead of a GET URL fragment/query.
- Added duplicate-submit protection and a loading state.
- Improved Dodo API error parsing and server logs.
