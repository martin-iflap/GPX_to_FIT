// The start-time control's "Avg speed" entry: one km/h box or a min:sec pace
// pair, a button to switch between them, and a quick-pick list. It only reads
// a speed; createTimeToggle (timeInput.js) turns that into a duration.
//
// The unit follows the sport (min/km for running, km/h otherwise) until the
// user picks one, the same `chosen ?? sport default` rule as profilePanel.js.

import { pad } from '../util/format.js';
import { createFocusDropdown } from './focusDropdown.js';
import { linkSegmentPair } from './dateTimeField.js';
import { createNumericInput, wireNumericBox } from './numericInput.js';

const SECONDS_MAX = 59;
// A tenth of a km/h is the finest anybody can honestly claim to hold.
const SPEED_STEP_KMH = 0.1;

// The quick-picks: the band a person on foot plausibly holds for a whole
// activity, so the common case is one click instead of typing. Anything
// outside it (or between two entries) is still typed into the boxes.
const PACE_PRESET_MIN_SECONDS = 3 * 60 + 30; // 3:30 /km
const PACE_PRESET_MAX_SECONDS = 7 * 60 + 30; // 7:30 /km
const PACE_PRESET_STEP_SECONDS = 30;
const SPEED_PRESET_MIN_KMH = 3;
const SPEED_PRESET_MAX_KMH = 10;
const SPEED_PRESET_STEP_KMH = 0.5;

/** @typedef {'pace'|'speed'} SpeedUnit */

/** The label a unit is written as, on the switch button. */
function unitLabel(unit) {
  return unit === 'speed' ? 'km/h' : 'min/km';
}

/**
 * The quick-pick list for one speed unit, in ascending label order (3:30 →
 * 7:30, 3.0 → 10.0) so each list reads the way its numbers do rather than
 * the way its speeds do — they run opposite ways, and the number is what the
 * eye scans. Each entry carries its own m/s so a pick can fill the boxes
 * through the same `writeSpeedMps` the unit switch uses.
 * @param {SpeedUnit} unit
 * @returns {{label: string, metersPerSecond: number}[]}
 */
function unitPresets(unit) {
  const presets = [];
  if (unit === 'speed') {
    // Counted in tenths rather than adding 0.5 repeatedly, which drifts into
    // values like 7.499999999999999 and then labels them "7.5" anyway.
    const stepTenths = SPEED_PRESET_STEP_KMH * 10;
    for (let tenths = SPEED_PRESET_MIN_KMH * 10; tenths <= SPEED_PRESET_MAX_KMH * 10; tenths += stepTenths) {
      const kmh = tenths / 10;
      presets.push({ label: kmh.toFixed(1), metersPerSecond: kmh / 3.6 });
    }
    return presets;
  }
  for (let seconds = PACE_PRESET_MIN_SECONDS; seconds <= PACE_PRESET_MAX_SECONDS; seconds += PACE_PRESET_STEP_SECONDS) {
    presets.push({ label: `${Math.floor(seconds / 60)}:${pad(seconds % 60)}`, metersPerSecond: 1000 / seconds });
  }
  return presets;
}

function createSuffix(text) {
  const el = document.createElement('span');
  el.className = 'input-suffix';
  el.textContent = text;
  return el;
}

/**
 * Builds the entry. The caller mounts `el` and shows or hides it.
 *
 * @param {object} opts
 * @param {() => string} opts.getSport - re-read on every `refresh()`
 * @param {() => void} opts.onInput - after any change of the speed: typing,
 *   a quick-pick, or a unit switch
 * @returns {{
 *   el: HTMLElement,
 *   getSpeedMps: () => number|null,
 *   getUnit: () => SpeedUnit,
 *   refresh: () => void,
 *   closePresets: () => void,
 *   focus: () => void,
 * }} `getSpeedMps()` is null while blank or zero. `refresh()` re-applies the
 *   sport's default unit (until the user picks one). `focus()` focuses and
 *   selects the first box of the unit in use.
 */
export function createSpeedEntry({ getSport, onInput }) {
  // Null until the user clicks a unit themselves; until then the sport picks it.
  let chosenUnit = null;

  // Both unit shapes are built once and the inactive one is just hidden, so
  // switching units is a class toggle rather than a rebuild — which keeps the
  // value each box holds while the user flips back and forth.
  const el = document.createElement('div');
  el.className = 'speed-fields hidden';

  // .duration-fields is reused for the row layout: same centered box+suffix
  // run as the h/m pair, which is exactly what these are. What is new is
  // .speed-entry, holding the typed boxes *only* — the trailing unit suffix
  // and the switch button are its siblings in the row. The quick-pick list
  // hangs from .speed-entry, so drawing the line there is what decides where
  // the list sits: anything else put inside it drags the list off the
  // numbers by half that thing's width.
  const kmhRow = document.createElement('div');
  kmhRow.className = 'duration-fields speed-fields-row speed-fields-kmh';
  const kmhEntry = document.createElement('div');
  kmhEntry.className = 'speed-entry';
  const kmhInput = createNumericInput('Average speed', '4.5');
  kmhEntry.append(kmhInput);
  kmhRow.append(kmhEntry, createSuffix('km/h'));

  const paceRow = document.createElement('div');
  paceRow.className = 'duration-fields speed-fields-row speed-fields-pace';
  const paceEntry = document.createElement('div');
  paceEntry.className = 'speed-entry';
  const paceMinutesInput = createNumericInput('Pace minutes', '5');
  const paceSecondsInput = createNumericInput('Pace seconds', '30');
  // The ':' stays inside: it sits *between* two boxes, so it is part of the
  // number, not a trailing unit label.
  paceEntry.append(paceMinutesInput, createSuffix(':'), paceSecondsInput);
  paceRow.append(paceEntry, createSuffix('/km'));

  el.append(kmhRow, paceRow);

  const speedInputs = [kmhInput, paceMinutesInput, paceSecondsInput];

  // One button rather than a two-option segmented control, sitting in the
  // entry row itself: it is always labeled with the unit it switches *to*,
  // so it states the alternative instead of restating what the suffix beside
  // it already says. `applyUnit` moves it into whichever row is visible.
  const unitButton = document.createElement('button');
  unitButton.type = 'button';
  unitButton.className = 'speed-unit-button';

  /** Which unit the boxes are in: the user's pick, else the sport's. */
  function currentUnit() {
    if (chosenUnit) {
      return chosenUnit;
    }
    return getSport() === 'running' ? 'pace' : 'speed';
  }

  /** Reads the boxes of one specific unit as m/s, or null if blank/zero. */
  function speedMpsFor(unit) {
    if (unit === 'speed') {
      const kmh = Number(kmhInput.value);
      return Number.isFinite(kmh) && kmh > 0 ? kmh / 3.6 : null;
    }
    const minutes = Number(paceMinutesInput.value) || 0;
    const seconds = Number(paceSecondsInput.value) || 0;
    const secondsPerKm = minutes * 60 + seconds;
    return secondsPerKm > 0 ? 1000 / secondsPerKm : null;
  }

  /** Writes a speed into one unit's boxes, e.g. when the unit is switched. */
  function writeSpeedMps(metersPerSecond, unit) {
    if (unit === 'speed') {
      kmhInput.value = (metersPerSecond * 3.6).toFixed(1);
      return;
    }
    const secondsPerKm = Math.round(1000 / metersPerSecond);
    paceMinutesInput.value = String(Math.floor(secondsPerKm / 60));
    paceSecondsInput.value = pad(secondsPerKm % 60);
  }

  /**
   * The preset nearest what's already entered, so a second visit doesn't
   * start back at the top of a 15-row list. Null with nothing entered.
   */
  function nearestPresetIndex(options) {
    const current = speedMpsFor(currentUnit());
    if (current === null) {
      return null;
    }
    let nearest = 0;
    options.forEach(({ value }, index) => {
      if (Math.abs(value.metersPerSecond - current) < Math.abs(options[nearest].value.metersPerSecond - current)) {
        nearest = index;
      }
    });
    return nearest;
  }

  // Quick-picks, opened by focusing any speed box, like the start time's
  // half-hours. Built for the unit current when it opens, so each option
  // carries that unit: a stale list would otherwise fill the wrong boxes.
  const presetDropdown = createFocusDropdown({
    triggers: speedInputs,
    className: 'dropdown speed-preset-dropdown',
    optionClassName: 'dropdown-option',
    getOptions: () => {
      const unit = currentUnit();
      return unitPresets(unit).map(({ label, metersPerSecond }) => ({ label, value: { metersPerSecond, unit } }));
    },
    getScrollIndex: nearestPresetIndex,
    onPick: ({ metersPerSecond, unit }) => {
      writeSpeedMps(metersPerSecond, unit);
      onInput();
    },
  });

  /**
   * Shows whichever unit's boxes are current, and moves the switch button and
   * the quick-picks into that row — they belong to the visible entry, not to
   * a unit, so there is only ever one of each in the DOM.
   */
  function applyUnit(unit) {
    kmhRow.classList.toggle('hidden', unit !== 'speed');
    paceRow.classList.toggle('hidden', unit !== 'pace');

    const activeRow = unit === 'speed' ? kmhRow : paceRow;
    const activeEntry = unit === 'speed' ? kmhEntry : paceEntry;
    // Guarded because the toggle re-applies the unit on every keystroke.
    // Re-appending an element already in place is still a DOM move (which
    // drops focus from it in some browsers), and the row only changes when
    // the unit does — which is also the only thing the label depends on.
    if (unitButton.parentElement === activeRow) {
      return;
    }
    const otherUnit = unit === 'speed' ? 'pace' : 'speed';
    unitButton.dataset.value = otherUnit;
    unitButton.textContent = unitLabel(otherUnit);
    unitButton.setAttribute('aria-label', `Switch to ${unitLabel(otherUnit)}`);
    unitButton.title = `Switch to ${unitLabel(otherUnit)}`;
    activeRow.append(unitButton);
    activeEntry.append(presetDropdown.el);
  }

  // Registered before the onInput listener so the caller always reads a
  // value the digit filter has already been through.
  wireNumericBox(kmhInput, { decimal: true, step: SPEED_STEP_KMH });
  wireNumericBox(paceMinutesInput);
  wireNumericBox(paceSecondsInput, { max: SECONDS_MAX });
  speedInputs.forEach((input) => input.addEventListener('input', () => onInput()));
  // The pace pair gets the same edge-arrow hopping as the h/m pair; the
  // single km/h box has nowhere to hop to.
  linkSegmentPair(paceMinutesInput, paceSecondsInput);

  unitButton.addEventListener('click', () => {
    // Read both before switching: `dataset.value` is the unit being moved
    // to, and the speed has to be read in the *old* unit so it carries
    // across instead of the number jumping to a different meaning.
    const unit = /** @type {SpeedUnit} */ (unitButton.dataset.value);
    const carried = speedMpsFor(currentUnit());
    chosenUnit = unit;
    // The open list belongs to the unit being left behind.
    presetDropdown.close();
    applyUnit(unit);
    if (carried !== null) {
      writeSpeedMps(carried, unit);
    }
    onInput();
  });

  applyUnit(currentUnit());

  return {
    el,
    getSpeedMps: () => speedMpsFor(currentUnit()),
    getUnit: currentUnit,
    refresh: () => applyUnit(currentUnit()),
    closePresets: () => presetDropdown.close(),
    focus() {
      const input = currentUnit() === 'speed' ? kmhInput : paceMinutesInput;
      input.focus();
      input.select();
    },
  };
}
