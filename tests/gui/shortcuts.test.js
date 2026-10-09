import assert from 'node:assert/strict';
import { beforeEach, describe, it } from 'node:test';

import './testUtils/domSetup.js';
import { initShortcuts } from '../../src/gpx2fit/gui/js/shortcuts.js';

/**
 * Builds just enough of index.html's sidebar for the shortcuts to have real
 * controls to click, and records a click log per control — the point of every
 * assertion below is that a keystroke reaches the *existing* control, not that
 * this module reimplements what the control does.
 *
 * Mounted exactly once for the whole file: initShortcuts binds to `document`,
 * and those listeners outlive any teardown of the elements, so a second mount
 * would leave the first one still reacting to every keystroke. `reset()` puts
 * the single instance back to a known state between tests instead.
 */
function mount() {
  const sportControlEl = document.createElement('div');
  sportControlEl.id = 'sportControl';
  const sportClicks = [];
  ['hiking', 'running'].forEach((value) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'segmented-option';
    button.dataset.value = value;
    button.addEventListener('click', () => sportClicks.push(value));
    sportControlEl.append(button);
  });

  const runButton = document.createElement('button');
  runButton.type = 'button';
  const runClicks = [];
  runButton.addEventListener('click', () => runClicks.push('run'));

  const gpxClicks = [];
  const photoClicks = [];
  function fileInput(log, tag) {
    const input = document.createElement('input');
    input.type = 'file';
    input.addEventListener('click', (event) => {
      // jsdom would otherwise try to open a real picker.
      event.preventDefault();
      log.push(tag);
    });
    return input;
  }
  const gpxFileInput = fileInput(gpxClicks, 'gpx');
  const photoFileInput = fileInput(photoClicks, 'photo');

  const deviceButton = document.createElement('button');
  deviceButton.type = 'button';
  const deviceClicks = [];
  deviceButton.addEventListener('click', () => deviceClicks.push('device'));

  const triggerButton = document.createElement('button');
  triggerButton.type = 'button';
  const panelContainer = document.createElement('div');

  // The start date/time field: a date box first, as createDateTimeField builds it.
  const startTimeFieldEl = document.createElement('div');
  const startDateInput = document.createElement('input');
  startDateInput.type = 'date';
  startTimeFieldEl.append(startDateInput);

  // The start-time toggle's shape (createTimeToggle): three tabs whose click
  // handlers switch `.is-active` and `.hidden`, and per-mode fields where
  // the speed mode hides its inactive unit row.
  const startTimeToggleEl = document.createElement('div');
  const modesEl = document.createElement('div');
  modesEl.className = 'time-toggle-modes segmented';
  const fieldsEl = document.createElement('div');
  fieldsEl.className = 'time-toggle-fields';
  const tabClicks = [];
  function modeFields(...inputs) {
    const el = document.createElement('div');
    el.append(...inputs);
    fieldsEl.append(el);
    return el;
  }
  function box(label) {
    const input = document.createElement('input');
    input.type = 'text';
    input.setAttribute('aria-label', label);
    return input;
  }
  const hoursInput = box('Hours');
  const endDateInput = box('End date');
  const kmhRow = document.createElement('div');
  kmhRow.className = 'hidden';
  kmhRow.append(box('km/h'));
  const paceMinutesInput = box('Pace minutes');
  const modes = [
    { name: 'duration', fields: modeFields(hoursInput, box('Minutes')) },
    { name: 'end', fields: modeFields(endDateInput) },
    { name: 'avgSpeed', fields: modeFields(kmhRow, paceMinutesInput) },
  ];
  modes.forEach(({ name }) => {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'segmented-option';
    tab.addEventListener('click', () => {
      tabClicks.push(name);
      setTimeMode(name);
    });
    modesEl.append(tab);
  });
  function setTimeMode(name) {
    modes.forEach((mode, index) => {
      modesEl.children[index].classList.toggle('is-active', mode.name === name);
      mode.fields.classList.toggle('hidden', mode.name !== name);
    });
  }
  startTimeToggleEl.append(modesEl, fieldsEl);

  const appShellEl = document.createElement('div');
  const profileCloseButton = document.createElement('button');
  profileCloseButton.type = 'button';
  const profileShowButton = document.createElement('button');
  profileShowButton.type = 'button';
  const profileClicks = [];
  profileCloseButton.addEventListener('click', () => profileClicks.push('close'));
  profileShowButton.addEventListener('click', () => profileClicks.push('show'));

  document.body.append(
    sportControlEl,
    runButton,
    gpxFileInput,
    photoFileInput,
    deviceButton,
    triggerButton,
    panelContainer,
    startTimeFieldEl,
    startTimeToggleEl,
    appShellEl,
    profileCloseButton,
    profileShowButton,
  );

  initShortcuts({
    triggerButton,
    panelContainer,
    sportControlEl,
    runButton,
    gpxFileInput,
    photoFileInput,
    deviceButton,
    startTimeFieldEl,
    startTimeToggleEl,
    appShellEl,
    profileCloseButton,
    profileShowButton,
  });

  const panel = panelContainer.querySelector('.shortcuts-panel');

  return {
    sportControlEl,
    sportClicks,
    runButton,
    runClicks,
    gpxClicks,
    photoClicks,
    deviceButton,
    deviceClicks,
    triggerButton,
    panel,
    startDateInput,
    hoursInput,
    endDateInput,
    paceMinutesInput,
    tabClicks,
    setTimeMode,
    appShellEl,
    profileCloseButton,
    profileShowButton,
    profileClicks,
    reset() {
      [sportClicks, runClicks, gpxClicks, photoClicks, deviceClicks, tabClicks, profileClicks].forEach((log) => log.splice(0));
      runButton.disabled = false;
      panel.classList.add('hidden');
      triggerButton.setAttribute('aria-expanded', 'false');
      setTimeMode('duration');
      appShellEl.classList.remove('has-profile');
      profileShowButton.hidden = true;
      document.activeElement?.blur();
    },
  };
}

const ctx = mount();

function press(init, target = document.body) {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

function isPanelOpen() {
  return !ctx.panel.classList.contains('hidden');
}

describe('initShortcuts', () => {
  beforeEach(() => ctx.reset());

  describe('sport switching', () => {
    it('Alt+H clicks the Hiking option and Alt+R the Running one', () => {
      press({ code: 'KeyH', altKey: true });
      press({ code: 'KeyR', altKey: true });
      assert.deepEqual(ctx.sportClicks, ['hiking', 'running']);
    });

    it('matches on event.code, so macOS Option+H ("˙") still works', () => {
      press({ code: 'KeyH', key: '˙', altKey: true });
      assert.deepEqual(ctx.sportClicks, ['hiking']);
    });

    it('ignores Alt+Shift+H and Ctrl+Alt+H', () => {
      press({ code: 'KeyH', altKey: true, shiftKey: true });
      press({ code: 'KeyH', altKey: true, ctrlKey: true });
      assert.deepEqual(ctx.sportClicks, []);
    });

    it('still fires while the user is focused in a text input', () => {
      // Alt-modified keys are unambiguous, unlike the plain "?" below.
      const input = document.createElement('input');
      document.body.append(input);
      press({ code: 'KeyR', altKey: true }, input);
      input.remove();
      assert.deepEqual(ctx.sportClicks, ['running']);
    });

    it('advertises the binding via aria-keyshortcuts', () => {
      const hiking = ctx.sportControlEl.querySelector('[data-value="hiking"]');
      assert.equal(hiking.getAttribute('aria-keyshortcuts'), 'Alt+H');
    });
  });

  describe('convert', () => {
    it('Ctrl+Enter clicks the convert button when it is enabled', () => {
      press({ key: 'Enter', ctrlKey: true });
      assert.deepEqual(ctx.runClicks, ['run']);
    });

    it('Cmd+Enter works too, for macOS', () => {
      press({ key: 'Enter', metaKey: true });
      assert.deepEqual(ctx.runClicks, ['run']);
    });

    it('does nothing while the button is disabled (not ready, or mid-conversion)', () => {
      ctx.runButton.disabled = true;
      const event = press({ key: 'Enter', ctrlKey: true });
      assert.deepEqual(ctx.runClicks, []);
      // Nothing happened, so the keystroke is left to the browser.
      assert.equal(event.defaultPrevented, false);
    });

    it('a bare Enter does not convert', () => {
      press({ key: 'Enter' });
      assert.deepEqual(ctx.runClicks, []);
    });
  });

  describe('file pickers', () => {
    it('Alt+U opens the GPX picker and Alt+P the photo picker', () => {
      press({ code: 'KeyU', altKey: true });
      press({ code: 'KeyP', altKey: true });
      assert.deepEqual(ctx.gpxClicks, ['gpx']);
      assert.deepEqual(ctx.photoClicks, ['photo']);
    });
  });

  describe('device picker', () => {
    it('Alt+W clicks the device button and closes the shortcuts panel', () => {
      press({ key: '?' });
      const event = press({ code: 'KeyW', altKey: true });
      assert.deepEqual(ctx.deviceClicks, ['device']);
      assert.equal(isPanelOpen(), false);
      assert.equal(event.defaultPrevented, true);
    });

    it('does nothing while a dialog is already open', () => {
      const dialog = document.createElement('dialog');
      dialog.setAttribute('open', '');
      document.body.append(dialog);
      press({ code: 'KeyW', altKey: true });
      dialog.remove();
      assert.deepEqual(ctx.deviceClicks, []);
    });

    it('advertises the binding via aria-keyshortcuts', () => {
      assert.equal(ctx.deviceButton.getAttribute('aria-keyshortcuts'), 'Alt+W');
    });
  });

  describe('start time and duration', () => {
    it('Alt+N focuses the start date box', () => {
      const event = press({ code: 'KeyN', altKey: true });
      assert.equal(document.activeElement, ctx.startDateInput);
      assert.equal(event.defaultPrevented, true);
    });

    it('Alt+L clicks the Duration tab and selects the hours box', () => {
      ctx.setTimeMode('end');
      ctx.hoursInput.value = '3';
      press({ code: 'KeyL', altKey: true });
      assert.deepEqual(ctx.tabClicks, ['duration']);
      assert.equal(document.activeElement, ctx.hoursInput);
      assert.equal(ctx.hoursInput.selectionStart, 0);
      assert.equal(ctx.hoursInput.selectionEnd, 1);
    });

    it('Alt+L works from inside another time box, since it has a modifier', () => {
      ctx.startDateInput.focus();
      press({ code: 'KeyL', altKey: true }, ctx.startDateInput);
      assert.equal(document.activeElement, ctx.hoursInput);
    });

    it('Alt+K steps through the modes and wraps back to Duration', () => {
      press({ code: 'KeyK', altKey: true });
      assert.equal(document.activeElement, ctx.endDateInput);
      press({ code: 'KeyK', altKey: true });
      // The km/h row is hidden, so the first box the user can see is the pace one.
      assert.equal(document.activeElement, ctx.paceMinutesInput);
      press({ code: 'KeyK', altKey: true });
      assert.equal(document.activeElement, ctx.hoursInput);
      assert.deepEqual(ctx.tabClicks, ['end', 'avgSpeed', 'duration']);
    });

    it('does nothing while a dialog is open', () => {
      const dialog = document.createElement('dialog');
      dialog.setAttribute('open', '');
      document.body.append(dialog);
      press({ code: 'KeyL', altKey: true });
      press({ code: 'KeyK', altKey: true });
      press({ code: 'KeyN', altKey: true });
      dialog.remove();
      assert.deepEqual(ctx.tabClicks, []);
      assert.equal(document.activeElement, document.body);
    });
  });

  describe('profile chart', () => {
    it('Alt+G hides an open profile', () => {
      ctx.appShellEl.classList.add('has-profile');
      const event = press({ code: 'KeyG', altKey: true });
      assert.deepEqual(ctx.profileClicks, ['close']);
      assert.equal(event.defaultPrevented, true);
    });

    it('Alt+G reopens a hidden one through the show button', () => {
      ctx.profileShowButton.hidden = false;
      press({ code: 'KeyG', altKey: true });
      assert.deepEqual(ctx.profileClicks, ['show']);
    });

    it('leaves the key alone before any conversion', () => {
      const event = press({ code: 'KeyG', altKey: true });
      assert.deepEqual(ctx.profileClicks, []);
      assert.equal(event.defaultPrevented, false);
    });

    it('advertises the binding on both buttons', () => {
      assert.equal(ctx.profileCloseButton.getAttribute('aria-keyshortcuts'), 'Alt+G');
      assert.equal(ctx.profileShowButton.getAttribute('aria-keyshortcuts'), 'Alt+G');
    });
  });

  describe('the shortcuts panel', () => {
    it('lists one row per bound shortcut', () => {
      assert.equal(isPanelOpen(), false);
      assert.equal(ctx.panel.querySelectorAll('.shortcut-row').length, 12);
      assert.equal(ctx.triggerButton.getAttribute('aria-expanded'), 'false');
    });

    it('opens and closes on the trigger button, keeping aria-expanded in sync', () => {
      ctx.triggerButton.click();
      assert.equal(isPanelOpen(), true);
      assert.equal(ctx.triggerButton.getAttribute('aria-expanded'), 'true');

      ctx.triggerButton.click();
      assert.equal(isPanelOpen(), false);
      assert.equal(ctx.triggerButton.getAttribute('aria-expanded'), 'false');
    });

    it('toggles on "?" and closes on Escape', () => {
      press({ key: '?' });
      assert.equal(isPanelOpen(), true);
      press({ key: 'Escape' });
      assert.equal(isPanelOpen(), false);
    });

    it('ignores "?" typed into an input, since it is a plain character', () => {
      const input = document.createElement('input');
      document.body.append(input);
      press({ key: '?' }, input);
      input.remove();
      assert.equal(isPanelOpen(), false);
    });

    it('leaves Escape to the field handlers while the panel is closed', () => {
      // dateTimeField.js blurs a time box on Escape — suppressing the event
      // here would break that.
      const event = press({ key: 'Escape' });
      assert.equal(event.defaultPrevented, false);
    });
  });

  it('ignores a keystroke another handler already consumed', () => {
    document.body.addEventListener('keydown', (event) => event.preventDefault(), { once: true });
    press({ code: 'KeyH', altKey: true });
    assert.deepEqual(ctx.sportClicks, []);
  });
});
