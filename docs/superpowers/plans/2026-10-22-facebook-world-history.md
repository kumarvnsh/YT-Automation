# Facebook world-history implementation plan

**Goal:** Deliver the approved regional evergreen content strategy and Facebook measurement without publishing a video during implementation.

**Architecture:** Reserve assignments when new stages are created, snapshot the profile into stage config, reuse citation validation and map manifests, and capture Facebook observations through existing analytics workflows.

- [x] Add failing rotation/profile tests in tests/test_editorial.py; implement src/editorial.py and pipeline/script prompt integration. Preserve manual/legacy behavior and avoid using YouTube retention examples as Facebook evidence.
- [x] Generate pinned Natural Earth maps, verify geometry provenance, render/view representative world maps, and fix separate SVG viewports. Derive research map IDs from the manifest and add verified institutional domains.
- [x] Add fail-first Facebook upload/caption tests. Publish Facebook first for primary Shorts; retain IDs and Facebook-only index records on secondary failure; fix state union identities with regressions.
- [x] Add normalized manual and native CSV importers, same-age source/definition-separated reporting, private baseline/earnings handling and analytics workflow integration.
- [x] Prepare page copy and an operating guide without modifying the live Page or posting comments.
- [x] Run full unittest discovery, diff checks and an offline stage/profile smoke check; review all changed files and confirm private data remains ignored.
- [ ] Commit and push verified changes to master. Do not dispatch a publishing workflow or paid generation job.

Primary verification: `.venv/bin/python -m unittest discover -s tests -t . -q` and `git diff --check`. Review new regional map bindings and verify both duration profiles use the same persisted target in generation and quality checks. Confirm the public report contains no private earnings and the native CSV cannot create age snapshots.
