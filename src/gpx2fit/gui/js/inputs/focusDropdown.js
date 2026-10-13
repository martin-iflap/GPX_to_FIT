// The quick-pick list that hangs under a field: the start and end time's
// half-hours, the average speed's presets and a multi-day activity's day
// selector. It costs no permanent room and never stands between the user and
// typing a value: it opens when a trigger is focused (or, for a button,
// clicked), fills on a click, and closes once focus has left both the
// triggers and the list.

/**
 * @template T
 * @param {object} opts
 * @param {HTMLElement[]} opts.triggers - the boxes (or the one button) the
 *   list belongs to. Focus moving between them keeps it open.
 * @param {() => {label: string, value: T}[]} opts.getOptions - rebuilt on
 *   every open, so the list can't go stale (the speed unit can change while
 *   it is closed)
 * @param {(value: T) => void} opts.onPick
 * @param {(options: {label: string, value: T}[]) => number|null} [opts.getScrollIndex] -
 *   the option to scroll to the top of the list on open, e.g. the one nearest
 *   what's already entered, so a second visit doesn't start back at the top
 * @param {'focus'|'click'} [opts.openOn] - `'focus'` for typeable boxes (the
 *   default; Esc closes by blurring), `'click'` for a button that toggles it
 * @param {string} opts.className - the list's classes
 * @param {string} opts.optionClassName
 * @returns {{el: HTMLElement, open: () => void, close: () => void}} the
 *   caller mounts `el` (and may move it, as the speed presets do)
 */
export function createFocusDropdown({
  triggers,
  getOptions,
  onPick,
  getScrollIndex,
  openOn = 'focus',
  className,
  optionClassName,
}) {
  const el = document.createElement('div');
  el.className = `${className} hidden`;

  // The trigger a pick hands focus back to: the one last focused, so picking
  // a pace doesn't move focus from the seconds box to the minutes box.
  let lastTrigger = triggers[0];
  // Set while a pick hands focus back, so that focus doesn't reopen the list.
  let refocusing = false;

  function close() {
    el.classList.add('hidden');
  }

  function open() {
    const options = getOptions();
    el.replaceChildren(
      ...options.map(({ label, value }) => {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = optionClassName;
        option.textContent = label;
        // The pick happens on 'click', and a mousedown that moved focus would
        // blur the trigger and close the list before the click lands.
        option.addEventListener('mousedown', (event) => event.preventDefault());
        option.addEventListener('click', () => {
          close();
          refocusing = true;
          lastTrigger.focus();
          refocusing = false;
          onPick(value);
        });
        return option;
      }),
    );
    el.classList.remove('hidden');

    const index = getScrollIndex?.(options);
    if (index !== null && index !== undefined && el.children[index]) {
      el.scrollTop = /** @type {HTMLElement} */ (el.children[index]).offsetTop;
    }
  }

  function closeIfFocusLeft() {
    // After the focus change has settled: during 'blur' activeElement is
    // still the old element (or the body).
    window.setTimeout(() => {
      const active = /** @type {HTMLElement|null} */ (document.activeElement);
      if (!triggers.includes(active) && !el.contains(active)) {
        close();
      }
    }, 0);
  }

  triggers.forEach((trigger) => {
    if (openOn === 'focus') {
      trigger.addEventListener('focus', () => {
        lastTrigger = trigger;
        if (!refocusing) {
          open();
        }
      });
      trigger.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          trigger.blur();
        }
      });
    } else {
      trigger.addEventListener('click', () => {
        lastTrigger = trigger;
        if (el.classList.contains('hidden')) {
          open();
        } else {
          close();
        }
      });
    }
    trigger.addEventListener('blur', closeIfFocusLeft);
  });
  // A keyboard user can Tab into the list; leaving it closes it the same way.
  el.addEventListener('focusout', closeIfFocusLeft);

  return { el, open, close };
}
