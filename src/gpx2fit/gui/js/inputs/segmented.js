// The segmented control (`.segmented` with one `.segmented-option` button per
// value, `data-value` naming it): the sport, the chart's unit and axis, the
// start-time modes and the stop popover's modes. The controls in index.html
// are wired with `wireSegmented`; the ones built at runtime come from
// `createSegmented`. Either way exactly one option is active, and the active
// one carries `.is-active`.
//
// Two kinds, which differ only in their ARIA (this module sets it all, so
// the markup carries none):
// - 'radio' (the default), for a plain choice: the sport, the chart's unit
//   and axis. A `radiogroup` of `radio`s, the chosen one `aria-checked`.
// - 'tabs', for a choice that swaps the panel below it: the start-time and
//   stop modes. A `tablist` of `tab`s, the chosen one `aria-selected`, and
//   `linkPanel` ties each tab to its panel.
// Both take the same keys: only the chosen option is in the tab order, and
// the arrows (wrapping), Home and End move the choice along with the focus.

const ROLES = {
  radio: { group: 'radiogroup', option: 'radio', state: 'aria-checked' },
  tabs: { group: 'tablist', option: 'tab', state: 'aria-selected' },
};

/** @type {Record<string, number>} */
const KEY_STEPS = {
  ArrowRight: 1,
  ArrowDown: 1,
  ArrowLeft: -1,
  ArrowUp: -1,
};

// For linkPanel's ids, unique across every control on the page.
let nextIdNumber = 1;

/**
 * Wires an existing segmented control: a click on an option selects it and
 * reports its value, and so does an arrow key, Home or End. `select` marks an
 * option without reporting it, for a value the code chose itself (a default,
 * or a state restored elsewhere).
 *
 * @param {HTMLElement} control - the `.segmented` element
 * @param {(value: string) => void} onSelect - called on every click, even on
 *   the option that is already active, and on a key that changes the choice
 * @param {{kind?: 'radio'|'tabs'}} [opts]
 * @returns {{select: (value: string) => void, getValue: () => string|null,
 *   buttonFor: (value: string) => HTMLButtonElement|null,
 *   linkPanel: (value: string, panel: HTMLElement) => void}}
 */
export function wireSegmented(control, onSelect, { kind = 'radio' } = {}) {
  const roles = ROLES[kind];
  const buttons = () => /** @type {HTMLButtonElement[]} */ ([...control.querySelectorAll('.segmented-option')]);
  const buttonFor = (value) => buttons().find((button) => button.dataset.value === value) ?? null;

  function select(value) {
    const all = buttons();
    // With no option active the first stays reachable by Tab.
    const tabbable = all.find((button) => button.dataset.value === value) ?? all[0];
    all.forEach((button) => {
      const active = button.dataset.value === value;
      button.classList.toggle('is-active', active);
      button.setAttribute(roles.state, String(active));
      button.tabIndex = button === tabbable ? 0 : -1;
    });
  }

  function getValue() {
    return buttons().find((button) => button.classList.contains('is-active'))?.dataset.value ?? null;
  }

  control.setAttribute('role', roles.group);
  buttons().forEach((button) => {
    button.setAttribute('role', roles.option);
    button.addEventListener('click', () => {
      const { value } = button.dataset;
      if (value !== undefined) {
        select(value);
        onSelect(value);
      }
    });
  });
  select(getValue());

  control.addEventListener('keydown', (event) => {
    const options = buttons().filter((button) => !button.hidden);
    // From the focused option: the key acts where the user is.
    const from = options.indexOf(/** @type {HTMLButtonElement} */ (event.target));
    if (from === -1) {
      return;
    }
    let to;
    if (event.key in KEY_STEPS) {
      to = (from + KEY_STEPS[event.key] + options.length) % options.length;
    } else if (event.key === 'Home') {
      to = 0;
    } else if (event.key === 'End') {
      to = options.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const target = options[to];
    target.focus();
    const { value } = target.dataset;
    if (value !== undefined && value !== getValue()) {
      select(value);
      onSelect(value);
    }
  });

  /**
   * Ties the tab for `value` to the panel it shows: `aria-controls` one way,
   * `aria-labelledby` the other. Ids are made up where missing.
   */
  function linkPanel(value, panel) {
    const tab = buttonFor(value);
    if (!tab) {
      return;
    }
    const idNumber = nextIdNumber++;
    tab.id ||= `segmented-option-${idNumber}`;
    panel.id ||= `segmented-panel-${idNumber}`;
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', tab.id);
    tab.setAttribute('aria-controls', panel.id);
  }

  return { select, getValue, buttonFor, linkPanel };
}

/**
 * Builds a segmented control and wires it like `wireSegmented`. The caller
 * mounts `el`.
 *
 * @param {object} opts
 * @param {{value: string, label: string}[]} opts.options - in display order
 * @param {string} opts.value - the option active at first
 * @param {(value: string) => void} opts.onSelect
 * @param {'radio'|'tabs'} [opts.kind] - see the top of this module
 * @param {string} [opts.className] - extra classes besides `.segmented`
 * @param {string} [opts.ariaLabel]
 * @returns {{el: HTMLElement} & ReturnType<typeof wireSegmented>}
 */
export function createSegmented({ options, value, onSelect, kind = 'radio', className = '', ariaLabel }) {
  const el = document.createElement('div');
  el.className = `segmented ${className}`.trim();
  if (ariaLabel) {
    el.setAttribute('aria-label', ariaLabel);
  }
  options.forEach((option) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'segmented-option';
    button.dataset.value = option.value;
    button.textContent = option.label;
    el.append(button);
  });

  const control = wireSegmented(el, onSelect, { kind });
  control.select(value);
  return { el, ...control };
}
