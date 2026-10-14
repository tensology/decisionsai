# Stencil: Funnel - SYNTHESIZED

Read the global `ui-ux-principles` skill first. This stencil applies only when the requirements explicitly call for a marketing, lead, or purchase funnel.

Compile an approved `ScreenContract` with the `funnel` executable pack before model-authored changes.

## Scope

- Define the conversion event and the source-to-conversion path before implementation.
- Preserve entered data when a multi-step funnel moves backward or recovers from a submission failure.
- Capture tracking and consent only when required by the approved measurement and privacy scope.

## Acceptance

- Given a funnel interaction reaches the defined conversion point, when the event is recorded, then it uses the approved event name and attribution source.
- Given a user moves backward or retries a failed step, when the prior step renders, then previously entered data remains available.
- Given tracking or consent is not in the approved scope, when the funnel renders, then no additional tracking or consent control is introduced.
- The feature is incomplete until the global Definition of Done - UI and applicable FRAC criteria pass.
