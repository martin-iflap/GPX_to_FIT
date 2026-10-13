// Where the placement popovers open: a Leaflet popup at the tapped point on a
// desktop (map.js's openPopup), and the same content in the #routeDialog modal
// on a phone. There a popup would be squeezed into the bit of map above the
// sheet, and Leaflet's 12px popup font makes iOS zoom into every input. The
// builders only ever see a container, close() and updateLayout(), so they
// don't know which one they're in.

import { requireChild } from '../util/dom.js';

/**
 * @callback BuildContent
 * @param {HTMLElement} container - empty; the content goes in here
 * @param {() => void} close - dismisses this content
 * @param {() => void} updateLayout - re-anchors a popup after the content
 *   changed size (the stop popover's widening); a dialog sizes itself
 */

/**
 * @param {object} deps
 * @param {{ openPopup: (lat: number, lon: number, build: BuildContent, opts: { onClose?: () => void }) => void }} deps.map
 * @param {HTMLDialogElement} deps.dialogEl - `#routeDialog`, with a
 *   `[data-route-close]` button and a `.route-dialog-body`
 * @param {() => boolean} deps.isPhoneLayout - asked on every open, since a
 *   rotated phone can cross the breakpoint
 * @returns {{ open: (lat: number, lon: number, build: BuildContent, opts?: { onClose?: () => void }) => void }}
 *   `onClose` runs once when the content goes away, however that happens:
 *   its own `close()`, the × or the backdrop, Esc, a click elsewhere on the
 *   map, or other content replacing it
 */
export function createPopoverHost({ map, dialogEl, isPhoneLayout }) {
  const body = requireChild(dialogEl, '.route-dialog-body');
  const closeButton = requireChild(dialogEl, '[data-route-close]');
  // The onClose of the content in the dialog, until it has run.
  /** @type {(() => void)|null} */
  let endCurrent = null;

  function endContent() {
    const onClose = endCurrent;
    endCurrent = null;
    onClose?.();
  }

  closeButton.addEventListener('click', () => dialogEl.close());
  // The panel fills the dialog, so a click whose target is the dialog itself
  // landed on the backdrop (the device picker does the same).
  dialogEl.addEventListener('click', (event) => {
    if (event.target === dialogEl) {
      dialogEl.close();
    }
  });
  // The close event comes a task after close(). By then content that closed
  // itself to open the next step (the pass picker, then a card) has reopened
  // the dialog, and that isn't the new content closing.
  dialogEl.addEventListener('close', () => {
    if (!dialogEl.open) {
      endContent();
    }
  });

  function openInDialog(build, onClose) {
    // Content replaced while the dialog is still open has gone too.
    endContent();
    endCurrent = onClose ?? null;
    body.replaceChildren();
    build(body, () => {
      endContent();
      dialogEl.close();
    }, () => {});
    if (!dialogEl.open) {
      dialogEl.showModal();
    }
  }

  return {
    open(lat, lon, build, { onClose } = {}) {
      if (isPhoneLayout()) {
        openInDialog(build, onClose);
      } else {
        map.openPopup(lat, lon, build, { onClose });
      }
    },
  };
}
