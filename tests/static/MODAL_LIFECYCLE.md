# Modal lifecycle regression coverage

This fix changes the bundled Bootstrap modal and modal manager together. Both original files match the copies captured from ShowroomBeam (beamOS 2.7.0, core plugin 2.10.0) on 2026-10-20.

## Behavioral contract

- Showing a modal does not wait for a backdrop animation event.
- A show requested during hide is queued until the old instance finishes teardown. A subsequent hide cancels that queued show.
- Modal transitions finish once, on the element's own transition event or a 500 ms fallback. Child events cannot finish the parent transition, and unrelated listeners are preserved.
- Once hidden, the wrapper and backdrop are removed synchronously. Destruction is idempotent; destroying an old instance cannot remove another stack entry.
- `hidden` and `destroy` listener failures cannot retain a completed hide's overlays. A destroy veto is honored for explicit destruction of an active dialog, but cannot prevent cleanup after a completed hide.
- A one-second ownership reconciliation removes old orphan layers, preserves hidden dialog elements and their bindings, and removes layers belonging to dialogs detached or directly hidden outside the modal API.
- Owned active dialogs, static modal backdrops, manager loading overlays and unknown visible dialogs are preserved. Recovery never resumes a job or changes machine state.

The 500 ms and one-second bounds assume the browser JavaScript event loop is running normally. Browser suspension/throttling can delay timers. This cannot guarantee responsiveness during a frozen browser or arbitrary application failure. An intentionally visible modal still blocks the underlying UI.

## Validation

From this directory, install the test-only dependency with `npm install`, then run `npm run test:modals`. The fixture uses the repository's actual jQuery and modal code, jsdom 26.1.0, and a deterministic clock. It intentionally does not emit CSS transition events unless a test requests them.

Twenty tests cover missing/bubbled/late events, rapid reopening, latest requested state, 200 cooling-style cycles, repeated teardown, stacked static dialogs, cancelled lifecycle events, external hide/detach, unknown integrations, exceptions, loading and replacement. The original source passes 2/20; the patched source passes 20/20. The source root may be overridden with MODAL_SOURCE_ROOT for the baseline comparison.

For real-browser verification, serve the repository root and open `/tests/static/js/modal-smoke.html`. The isolated page has no connection to a cutter. It tests missing transitions, rapid reopening, 200 repeated cycles, recovery of exactly three orphan pairs (one containing the hidden dialog), and button hit testing. All six checks passed in Chrome on 2026-10-20. A separate automation attempt to click the underlying button timed out, so the validated interaction result is browser DOM hit testing, not a confirmed physical automation click.

## Rollout

Ship both JS files in the OctoPrint component in the beamOS image. Rebuild/invalidate the normal bundled asset cache and reload clients so they load both updated files; copying files alone does not update an already-running page. This patch is not installed on ShowroomBeam. Before a production release, exercise real application dialogs (login, settings, job preparation, cooling, cancellation and stacked notifications), including background-tab operation, with this OctoPrint build. Hardware cooling reproduction was not performed.

The change makes backdrop removal immediate instead of waiting for a fade-out and displays the dialog immediately instead of waiting for the backdrop fade-in. Modal fade-out itself retains its bounded transition. The visible application behavior of third-party plugins that depend on the old lifecycle ordering needs integration testing.
