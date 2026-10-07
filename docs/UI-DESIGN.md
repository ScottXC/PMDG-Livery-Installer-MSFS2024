# v0.1.6 UI delivery plan

The existing Tk application has working package transactions and worker-thread handling, but dense nested panels and a text table make browsing a livery library difficult. Keep its native Python/Tk deployment and backend; rebuilding it in a browser shell or Qt would add a second migration without helping this release's workflows.

Design references inspected: [PMDG OC3 Liveries](https://manuals.pmdg.com/oc3/#/lessons/Mjx_-eZYWllQnVmHjyRrnlj2J9Z-J1W9), [Fenix Livery Manager designer portfolio](https://sgcsam.com/portfolios/prof/FenixLiveryManager/), and [Fenix installer guide](https://support.fenixsim.com/hc/en-us/articles/12459059815823-New-Fenix-Installer). Reuse navigation and browsing ideas, with original project branding and icons.

Execution:

1. Build a charcoal shell with icon navigation, clear page headings, an aircraft selector, persistent task status, and a responsive thumbnail gallery. Keep keyboard selection, multi-select, search, empty states, and an optional table view.
2. Rebuild Install around source, target, review, and progress; provide clearly labeled diagnostics and recovery actions. Draw a new scalable tail-fin application mark and a consistent navigation icon set.
3. Set all release/version metadata to 0.1.6. Run the existing backend/GUI regressions, add gallery interaction coverage, correct observed clipping or unreadable states. Further Computer Use verification was waived by the maintainer; do not claim a completed size/scale visual test matrix.
4. Build and smoke-test the executable and installer. Package the flightsim.to ZIP, usage/changelog text, and listing copy. Commit and push the reviewed source, then publish GitHub release v0.1.6 with the deliverables.

Validation order: MCAF UI/UX review (navigation, contrast, keyboard and scaling); real Tk regression tests (state and worker integration); Computer Use checks stopped at the maintainer's request; packaged executable workflows; archive contents/version checks; GitHub publication verification. Web-specific frontend test skills do not apply to this Tk application. No new test-generation skill is required because this repository already has unittest and GUI smoke coverage.

Visual tokens: background #15171c; sidebar #101217; surface #20232b; raised #292d36; border #363d4a; text #f3f5f8; muted #abb4c4; primary #376dc5; primary text white; cyan #75d4ed; destructive #b63b53. Use Segoe UI, visible keyboard focus, 8px spacing increments, and readable text labels beside icons. Color is never the only status indicator. The preview utility uses synthetic demonstration packages and original illustrative thumbnails, never a user's simulator library. No screenshots from earlier releases are included as v0.1.6 screenshots.
