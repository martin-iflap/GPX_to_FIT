// Global keyboard shortcuts and the small panel that documents them.
//
// Two rules shape this module:
//
// 1. **Every shortcut delegates to an existing control** — it calls `.click()`
//    on the real button, or the start-time field's and toggle's own focus
//    methods, rather than reproducing what they do or reaching into their
//    markup. So `main.js` stays the single source of truth for sport
//    selection and conversion, and a shortcut can never drift out of sync
//    with the mouse path (including the `disabled` guard that covers both
//    "not ready" and "mid-conversion").
// 2. **One list (SHORTCUTS below) binds the keys, runs them, generates the
//    panel and sets `aria-keyshortcuts`**, so what's documented is by
//    construction what's bound, and a new shortcut is one entry.
//
// Key choices worth recording, since they're not arbitrary:
// - Ctrl+Shift+R is a browser hard-reload and is NOT preventable by a page,
//   so the sport shortcuts use Alt instead.
// - Alt+D/E/F/T/V/B/S are browser menu or address-bar accelerators and are
//   deliberately avoided (so the device picker is Alt+W, for "watch", not
//   Alt+D, and the duration box is Alt+L, for "length"); Alt+H/R/U/P/W and
//   Alt+L/K/N/G are free (Alt+H opens Firefox's Help menu when its menu bar
//   is reachable, which the preventDefault below suppresses).
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

/** Clicks a control on the user's behalf, which then did something. */
function click(control) {
  control.click();
  return true;
}

function clickSport(controls, value) {
  const button = controls.sportControl.buttonFor(value);
  return Boolean(button) && click(button);
}

/**
 * @typedef {object} Controls - what initShortcuts was given, plus the
 *   panel's own `isPanelOpen()` and `setPanelOpen(open)`
 *
 * @typedef {object} Shortcut
 * @property {string[]} keys - display only, rendered as <kbd> chips
 * @property {string} label
 * @property {(event: KeyboardEvent) => boolean} match - what actually fires
 * @property {(c: Controls) => boolean} run - whether it did something; only
 *   then is the browser's own handling of the keystroke suppressed
 * @property {(c: Controls) => (HTMLElement|null)[]} [targets] - the controls
 *   it acts for, which advertise it through `aria-keyshortcuts`
 * @property {string} [aria] - that attribute's value, when it isn't `keys`
 *   joined with "+"
 * @property {boolean} [plain] - no modifier, so it must not fire while the
 *   user is typing; with a modifier there's no such ambiguity, so Alt/Ctrl
 *   shortcuts stay live inside the time fields
 */

/** @type {Shortcut[]} */
const SHORTCUTS = [
  {
    keys: ['Alt', 'H'],
    label: 'Sport: Hiking',
    match: (e) => isAltCombo(e, 'KeyH'),
    run: (c) => clickSport(c, 'hiking'),
    targets: (c) => [c.sportControl.buttonFor('hiking')],
  },
  {
    keys: ['Alt', 'R'],
    label: 'Sport: Running',
    match: (e) => isAltCombo(e, 'KeyR'),
    run: (c) => clickSport(c, 'running'),
    targets: (c) => [c.sportControl.buttonFor('running')],
  },
  {
    keys: ['Ctrl', 'Enter'],
    label: 'Convert to FIT',
    match: isPrimaryEnter,
    // `disabled` already encodes both "no route / no valid start time"
    // (updateConvertAvailability) and "conversion in flight" (the convert
    // handler sets it for the duration), so this needs no state of its own.
    run: (c) => !c.runButton.disabled && click(c.runButton),
    targets: (c) => [c.runButton],
    aria: 'Control+Enter Meta+Enter',
  },
  {
    keys: ['Alt', 'U'],
    label: 'Choose a GPX file',
    match: (e) => isAltCombo(e, 'KeyU'),
    // A keydown counts as a user gesture, so opening the picker
    // programmatically from here is allowed.
    run: (c) => click(c.gpxFileInput),
  },
  {
    keys: ['Alt', 'P'],
    label: 'Add photos',
    match: (e) => isAltCombo(e, 'KeyP'),
    run: (c) => click(c.photoFileInput),
  },
  {
    keys: ['Alt', 'W'],
    label: 'Choose a device',
    match: (e) => isAltCombo(e, 'KeyW'),
    run: (c) => {
      c.setPanelOpen(false);
      return click(c.deviceButton);
    },
    targets: (c) => [c.deviceButton],
  },
  {
    keys: ['Alt', 'N'],
    label: 'Enter the start time',
    match: (e) => isAltCombo(e, 'KeyN'),
    run: (c) => {
      c.setPanelOpen(false);
      c.startTimeField.focus();
      return true;
    },
  },
  {
    keys: ['Alt', 'L'],
    label: 'Enter the duration',
    match: (e) => isAltCombo(e, 'KeyL'),
    run: (c) => {
      c.setPanelOpen(false);
      return c.startTimeToggle.focusMode('duration');
    },
  },
  {
    keys: ['Alt', 'K'],
    label: 'Next of Duration / End time / Avg speed',
    match: (e) => isAltCombo(e, 'KeyK'),
    run: (c) => {
      c.setPanelOpen(false);
      return c.startTimeToggle.nextMode();
    },
  },
  {
    keys: ['Alt', 'G'],
    label: 'Show or hide the profile chart',
    match: (e) => isAltCombo(e, 'KeyG'),
    // Both buttons already handle focus and the show pill; with no
    // conversion yet neither applies, and the key is left alone.
    run: (c) => {
      if (c.appShellEl.classList.contains('has-profile')) {
        return click(c.profileCloseButton);
      }
      return !c.profileShowButton.hidden && click(c.profileShowButton);
    },
    targets: (c) => [c.profileCloseButton, c.profileShowButton],
  },
  {
    keys: ['?'],
    label: 'Show these shortcuts',
    plain: true,
    match: (e) => e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey,
    run: (c) => {
      c.setPanelOpen(!c.isPanelOpen());
      return true;
    },
  },
  {
    keys: ['Esc'],
    label: 'Close this panel',
    plain: true,
    match: (e) => e.key === 'Escape',
    run: (c) => {
      if (!c.isPanelOpen()) {
        return false;
      }
      c.setPanelOpen(false);
      return true;
    },
  },
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

/**
 * Wires the global shortcut handler and builds the shortcuts panel into
 * `panelContainer`. Call once at startup, once the controls exist.
 *
 * Every control passed in is one of the shortcuts act through on behalf of the
 * user — this module never reads or writes app state itself.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.triggerButton - the header button that toggles the panel
 * @param {HTMLElement} opts.panelContainer - empty element the panel mounts into
 * @param {ReturnType<import('../inputs/segmented.js').wireSegmented>} opts.sportControl -
 *   the wired #sportControl
 * @param {HTMLButtonElement} opts.runButton - the Convert button
 * @param {HTMLInputElement} opts.gpxFileInput - the hidden GPX file input
 * @param {HTMLInputElement} opts.photoFileInput - the hidden photo file input
 * @param {HTMLButtonElement} opts.deviceButton - the sidebar button that opens the device picker
 * @param {{ focus: () => void }} opts.startTimeField - createDateTimeField's
 * @param {{ focusMode: (mode: string) => boolean, nextMode: () => boolean }} opts.startTimeToggle -
 *   createTimeToggle's
 * @param {HTMLElement} opts.appShellEl - the app shell, which carries `.has-profile` while the profile panel is open
 * @param {HTMLButtonElement} opts.profileCloseButton - hides the profile panel
 * @param {HTMLButtonElement} opts.profileShowButton - reopens it; `hidden` while there's nothing to show
 */
export function initShortcuts(opts) {
  const { triggerButton, panelContainer } = opts;

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
    const target = /** @type {Node} */ (event.target);
    if (!isPanelOpen() || panel.contains(target) || triggerButton.contains(target)) {
      return;
    }
    setPanelOpen(false);
  });

  /** @type {Controls} */
  const controls = { ...opts, isPanelOpen, setPanelOpen };

  /* ---------- global key handler ---------- */

  document.addEventListener('keydown', (event) => {
    // A modal dialog (the device picker) makes the page behind it inert, and
    // its shortcuts with it: Ctrl+Enter mustn't convert from under the popup.
    if (event.defaultPrevented || document.querySelector('dialog[open]')) {
      return;
    }
    for (const shortcut of SHORTCUTS) {
      if (!shortcut.match(event)) {
        continue;
      }
      if (shortcut.plain && isEditableTarget(event.target)) {
        return;
      }
      if (shortcut.run(controls)) {
        event.preventDefault();
      }
      return;
    }
  });

  /* ---------- advertise the bindings to assistive tech ---------- */

  SHORTCUTS.forEach(({ keys, targets, aria }) => {
    targets?.(controls).forEach((target) => target?.setAttribute('aria-keyshortcuts', aria ?? keys.join('+')));
  });
}
