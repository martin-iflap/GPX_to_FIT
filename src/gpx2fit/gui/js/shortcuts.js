// Global keyboard shortcuts and the small panel that documents them.
//
// Two rules shape this module:
//
// 1. **Every shortcut delegates to an existing control** — it calls `.click()`
//    on the real button rather than reproducing what that button's handler
//    does. So `main.js` stays the single source of truth for sport selection
//    and conversion, and a shortcut can never drift out of sync with the
//    mouse path (including the `disabled` guard that covers both "not ready"
//    and "mid-conversion").
// 2. **The panel is generated from the same list that binds the keys**
//    (SHORTCUTS below), so what's documented is by construction what's bound.
//
// Key choices worth recording, since they're not arbitrary:
// - Ctrl+Shift+R is a browser hard-reload and is NOT preventable by a page,
//   so the sport shortcuts use Alt instead.
// - Alt+D/E/F/T/V/B/S are browser menu or address-bar accelerators and are
//   deliberately avoided; Alt+H/R/U/P are free (Alt+H opens Firefox's Help
//   menu when its menu bar is reachable, which the preventDefault below
//   suppresses).
// - Matching is on `event.code`, not `event.key`, because Option+letter on
//   macOS produces a special character ("˙" for Option+H) rather than the
//   letter itself.

const IS_APPLE = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || '');

/** Alt combo with no other modifier — Alt+Shift+H should not switch sport. */
function isAltCombo(event, code) {
  return event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.code === code;
}

/** The platform's "primary" modifier + Enter: Ctrl+Enter, or Cmd+Enter on a Mac. */
function isPrimaryEnter(event) {
  return (event.ctrlKey || event.metaKey) && !event.altKey && event.key === 'Enter';
}

/** Whether the keystroke landed in something the user is typing into. */
function isEditableTarget(target) {
  if (!target || typeof target.tagName !== 'string') {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  const tag = target.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select';
}

// `keys` is display-only (rendered as <kbd> chips); `match` is what actually
// fires. `plain` marks a shortcut with no modifier, which must not fire while
// the user is typing — with a modifier there's no such ambiguity, so Alt/Ctrl
// shortcuts stay live inside the time and device fields.
const SHORTCUTS = [
  { keys: ['Alt', 'H'], label: 'Sport: Hiking', action: 'hiking', match: (e) => isAltCombo(e, 'KeyH') },
  { keys: ['Alt', 'R'], label: 'Sport: Running', action: 'running', match: (e) => isAltCombo(e, 'KeyR') },
  { keys: ['Ctrl', 'Enter'], label: 'Convert to FIT', action: 'convert', match: isPrimaryEnter },
  { keys: ['Alt', 'U'], label: 'Choose a GPX file', action: 'uploadGpx', match: (e) => isAltCombo(e, 'KeyU') },
  { keys: ['Alt', 'P'], label: 'Add photos', action: 'uploadPhotos', match: (e) => isAltCombo(e, 'KeyP') },
  { keys: ['?'], label: 'Show these shortcuts', action: 'togglePanel', plain: true, match: (e) => e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey },
  { keys: ['Esc'], label: 'Close this panel', action: 'closePanel', plain: true, match: (e) => e.key === 'Escape' },
];

/** Ctrl reads as ⌘ and Alt as ⌥ on Apple keyboards; everything else is literal. */
function displayKey(token) {
  if (!IS_APPLE) {
    return token;
  }
  if (token === 'Ctrl') {
    return '⌘';
  }
  if (token === 'Alt') {
    return '⌥';
  }
  return token;
}

/** The `aria-keyshortcuts` value for a shortcut, e.g. "Alt+H". */
function ariaKeyshortcuts(keys) {
  return keys.join('+');
}

/**
 * Wires the global shortcut handler and builds the shortcuts panel into
 * `panelContainer`. Call once at startup, like initTheme.
 *
 * Every element passed in is one of the shortcuts click on behalf of the user —
 * this module never reads or writes app state itself.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.triggerButton - the header button that toggles the panel
 * @param {HTMLElement} opts.panelContainer - empty element the panel mounts into
 * @param {HTMLElement} opts.sportControlEl - the #sportControl segmented control
 * @param {HTMLButtonElement} opts.runButton - the Convert button
 * @param {HTMLInputElement} opts.gpxFileInput - the hidden GPX file input
 * @param {HTMLInputElement} opts.photoFileInput - the hidden photo file input
 */
export function initShortcuts({
  triggerButton,
  panelContainer,
  sportControlEl,
  runButton,
  gpxFileInput,
  photoFileInput,
}) {
  /* ---------- panel ---------- */

  const panel = document.createElement('div');
  panel.className = 'shortcuts-panel hidden';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Keyboard shortcuts');

  const heading = document.createElement('p');
  heading.className = 'shortcuts-panel-heading';
  heading.textContent = 'Keyboard shortcuts';
  panel.append(heading);

  SHORTCUTS.forEach(({ keys, label }) => {
    const row = document.createElement('div');
    row.className = 'shortcut-row';

    const keysEl = document.createElement('span');
    keysEl.className = 'shortcut-keys';
    keys.forEach((token) => {
      const kbd = document.createElement('kbd');
      kbd.textContent = displayKey(token);
      keysEl.append(kbd);
    });

    const labelEl = document.createElement('span');
    labelEl.className = 'shortcut-label';
    labelEl.textContent = label;

    row.append(keysEl, labelEl);
    panel.append(row);
  });

  panelContainer.append(panel);

  function isPanelOpen() {
    return !panel.classList.contains('hidden');
  }

  function setPanelOpen(open) {
    panel.classList.toggle('hidden', !open);
    triggerButton.setAttribute('aria-expanded', String(open));
  }

  triggerButton.addEventListener('click', () => setPanelOpen(!isPanelOpen()));

  // Same dismiss-on-outside-interaction shape as the time/day dropdowns, but
  // keyed off pointerdown rather than blur: the panel holds no focusable
  // content of its own, so there is no blur to hang it on.
  document.addEventListener('pointerdown', (event) => {
    if (!isPanelOpen() || panel.contains(event.target) || triggerButton.contains(event.target)) {
      return;
    }
    setPanelOpen(false);
  });

  /* ---------- actions ---------- */

  function clickSport(value) {
    const button = sportControlEl.querySelector(`.segmented-option[data-value="${value}"]`);
    if (!button) {
      return false;
    }
    button.click();
    return true;
  }

  // Returns whether the shortcut actually did something — only then is the
  // browser's own handling of the keystroke suppressed.
  function runAction(action) {
    switch (action) {
      case 'hiking':
        return clickSport('hiking');
      case 'running':
        return clickSport('running');
      case 'convert':
        // `disabled` already encodes both "no route / no valid start time"
        // (updateConvertAvailability) and "conversion in flight" (the convert
        // handler sets it for the duration), so this needs no state of its own.
        if (runButton.disabled) {
          return false;
        }
        runButton.click();
        return true;
      case 'uploadGpx':
        // A keydown counts as a user gesture, so opening the picker
        // programmatically from here is allowed.
        gpxFileInput.click();
        return true;
      case 'uploadPhotos':
        photoFileInput.click();
        return true;
      case 'togglePanel':
        setPanelOpen(!isPanelOpen());
        return true;
      case 'closePanel':
        if (!isPanelOpen()) {
          return false;
        }
        setPanelOpen(false);
        return true;
      default:
        return false;
    }
  }

  /* ---------- global key handler ---------- */

  document.addEventListener('keydown', (event) => {
    if (event.defaultPrevented) {
      return;
    }
    for (const shortcut of SHORTCUTS) {
      if (!shortcut.match(event)) {
        continue;
      }
      if (shortcut.plain && isEditableTarget(event.target)) {
        return;
      }
      if (runAction(shortcut.action)) {
        event.preventDefault();
      }
      return;
    }
  });

  /* ---------- advertise the bindings to assistive tech ---------- */

  SHORTCUTS.forEach(({ keys, action }) => {
    if (action === 'hiking' || action === 'running') {
      const button = sportControlEl.querySelector(`.segmented-option[data-value="${action}"]`);
      button?.setAttribute('aria-keyshortcuts', ariaKeyshortcuts(keys));
    } else if (action === 'convert') {
      runButton.setAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter');
    }
  });
}
