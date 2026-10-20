# UI Taste Log

Record only user corrections, rejections, or requested rework. Do not record inferred preferences.

## Entry format

`YYYY-MM-DD | Did: <decision> | Wanted: <replacement> | Why: <reason>`

## Entries

2026-10-14 | Did: Treated broad palette and contrast guidance as sufficient | Wanted: Explicit visual pairing and complete light/dark-theme styling across every component and state | Why: Mismatched foreground/background colors, visual misalignment, and forgotten theme styling are major UI faults
2026-10-19 | Did: Changed committed header navigation and filled gaps with invented menu choices while implementing a narrower request | Wanted: Preserve the approved header and make only the requested navigation change, using the committed implementation as the reference when wording is unclear | Why: Unrequested navigation changes break product continuity and force avoidable rework
2026-10-19 | Did: Treated successful builds, partial checks, or a representative screenshot as proof that a UI request was finished | Wanted: Reconcile every requested item against the final product and verify every affected route, role, state, and viewport before reporting completion | Why: Several projects were declared complete while requested pages, mobile interactions, document items, or visible defects remained
2026-10-19 | Did: Compressed desktop layouts into mobile widths, leaving panels, previews, imagery, and controls cut off or unusable | Wanted: Recompose mobile around task order, remove desktop-only content, preserve meaningful geometry, and test real interaction at mobile width | Why: A narrow screenshot is not a responsive product when core content or interaction is clipped
2026-10-19 | Did: Built related public and account pages with separate shells, inconsistent controls, and one-off title treatments | Wanted: Reuse the product's shared shell, navigation, form controls, title hierarchy, and interaction language across related routes | Why: Parallel templates make one product feel like disconnected sites and multiply visual defects
