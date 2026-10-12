// The segmented control (`.segmented` with one `.segmented-option` button per
// value, `data-value` naming it): the sport, the chart's unit and axis, the
// start-time modes and the stop popover's modes. The controls in index.html
// are wired with `wireSegmented`; the ones built at runtime come from
// `createSegmented`. Either way exactly one option is active, and the active
// one carries `.is-active` and `aria-selected="true"`.

/**
 * Wires an existing segmented control: a click on an option selects it and
 * reports its value. `select` marks an option without reporting it, for a
 * value the code chose itself (a default, or a state restored elsewhere).
 *
 * @param {HTMLElement} control - the `.segmented` element
 * @param {(value: string) => void} onSelect - called on every click, even on
 *   the option that is already active
 * @returns {{select: (value: string) => void, getValue: () => string|null,
 *   buttonFor: (value: string) => HTMLButtonElement|null}}
 */
export function wireSegmented(control, onSelect) {
  const buttons = () => [...control.querySelectorAll('.segmented-option')];

  function select(value) {
    buttons().forEach((button) => {
      const active = button.dataset.value === value;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', String(active));
    });
  }

  buttons().forEach((button) => {
    button.addEventListener('click', () => {
      select(button.dataset.value);
      onSelect(button.dataset.value);
    });
  });

  return {
    select,
    getValue: () => buttons().find((button) => button.classList.contains('is-active'))?.dataset.value ?? null,
    buttonFor: (value) => buttons().find((button) => button.dataset.value === value) ?? null,
  };
}

/**
 * Builds a segmented control and wires it like `wireSegmented`. The caller
 * mounts `el`.
 *
 * @param {object} opts
 * @param {{value: string, label: string}[]} opts.options - in display order
 * @param {string} opts.value - the option active at first
 * @param {(value: string) => void} opts.onSelect
 * @param {string} [opts.className] - extra classes besides `.segmented`
 * @param {string} [opts.ariaLabel]
 * @returns {{el: HTMLElement, select: (value: string) => void, getValue: () => string|null,
 *   buttonFor: (value: string) => HTMLButtonElement|null}}
 */
export function createSegmented({ options, value, onSelect, className = '', ariaLabel }) {
  const el = document.createElement('div');
  el.className = `segmented ${className}`.trim();
  el.setAttribute('role', 'tablist');
  if (ariaLabel) {
    el.setAttribute('aria-label', ariaLabel);
  }
  options.forEach((option) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'segmented-option';
    button.dataset.value = option.value;
    button.textContent = option.label;
    button.setAttribute('role', 'tab');
    el.append(button);
  });

  const control = wireSegmented(el, onSelect);
  control.select(value);
  return { el, ...control };
}
