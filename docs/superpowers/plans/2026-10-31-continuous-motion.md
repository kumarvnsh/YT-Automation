# Continuous motion for future Histold videos

Approved scope: future videos only; do not edit or republish Santa Fe.

The Santa Fe opening used the local house fallback. Its 2% map zoom and full narration title looked static. Motion warnings did not trigger repair. Replace that fallback with a continuously animated diagram/map composition, concise timed narration excerpts and a visible progress rail. Retain vetted geography and readable labels. Request the same approach from generated scenes. Sample early motion and promote static intervals to validation errors so scenes are repaired/recovered before publication.

- [x] Add regression coverage for early/stalled motion and real fallback rendering.
- [x] Implement animated recovery and enforce motion during scene inspection.
- [x] Check browser timeline frames, existing renderer tests, and publish checks.
- [x] Push only these changes for future dispatches.

Validation: 348 unit tests passed. Browser timeline checks passed for single-country, separate-world, shared-projection and non-map recovery scenes; static/delayed opening rejection passed. Inspected opening screenshot. Recovery upload 41168548000 finished successfully before this renderer change.
