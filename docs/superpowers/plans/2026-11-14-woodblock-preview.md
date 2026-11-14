# Woodblock Preview Implementation Plan

**Goal:** Deliver one unpublished woodblock-print Histold MP4 from the existing GitHub preview workflow.
**Architecture:** Add an optional woodblock art style to cinematic prompt and palette preparation; select it through the preview script and workflow, preserving the default style.
**Tech Stack:** Python, CSS/SVG, Playwright, FFmpeg, GitHub Actions.

- [ ] Add failing tests in tests/test_cinematic_motion.py for the woodblock prompt and palette while retaining default tests.
- [ ] Add optional art_style to system_prompt and prepare_html in src/cinematic_motion.py; thread video.claude_motion.art_style through src/claude_motion.py generation, repair and preparation.
- [ ] Add a style choice to .github/workflows/cinematic-preview.yml and scripts/cinematic_preview.py; keep no-upload behavior.
- [ ] Run unittest discovery and diff checks, commit and push the preview branch.
- [ ] Dispatch cinematic-preview.yml on that branch using source_run 43567776874 and style woodblock; monitor, inspect the artifact video and deliver it.
