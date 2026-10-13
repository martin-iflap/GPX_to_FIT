// The pieces every placement popover is built from: a heading, a hint, the
// confirm button, a two-line option, a card's pair of buttons, and the photo
// header. Each returns a detached element for the caller to mount, so the
// popovers (anchorPopovers.js, stopPopover.js, candidatePicker.js) read as
// layout rather than createElement calls.

import { formatClockSeconds } from '../util/format.js';

/** @param {string} text */
export function popoverHeading(text) {
  const heading = document.createElement('p');
  heading.className = 'popover-heading';
  heading.textContent = text;
  return heading;
}

/**
 * @param {string} text
 * @param {{ warning?: boolean, hidden?: boolean }} [opts] - `warning` styles
 *   it amber; `hidden` for one shown later, once there's something to warn about
 */
export function popoverHint(text, { warning = false, hidden = false } = {}) {
  const hint = document.createElement('p');
  hint.className = warning ? 'popover-hint is-warning' : 'popover-hint';
  hint.textContent = text;
  hint.hidden = hidden;
  return hint;
}

/**
 * Prefilled times are a straight-line average-pace guess, not a fact: this
 * says so wherever a popover prefills from one, so it isn't mistaken for a
 * precise computed arrival time.
 *
 * @param {Date|null} estimate
 * @returns {HTMLElement|null} null when nothing was prefilled
 */
export function estimateHint(estimate) {
  return estimate ? popoverHint('Pre-filled time is an estimate — check and adjust.') : null;
}

/** The one thing a popover is for: "Add anchor", "Place photo here"… */
export function confirmButton(text) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'primary-button popover-confirm';
  button.textContent = text;
  return button;
}

/**
 * A choice in a list (`.candidate-list`): a title and a quieter line of
 * detail under it, e.g. a pass's distance and its estimated time.
 *
 * @param {string} title
 * @param {string} detail
 */
export function optionButton(title, detail) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'candidate-option';
  const titleEl = document.createElement('span');
  titleEl.className = 'candidate-distance';
  titleEl.textContent = title;
  const detailEl = document.createElement('span');
  detailEl.className = 'candidate-estimate';
  detailEl.textContent = detail;
  button.append(titleEl, detailEl);
  return button;
}

/**
 * A card's two buttons: a quiet way out, and the one thing it's for.
 *
 * @param {string} cancelText
 * @param {string} confirmText
 * @returns {{ el: HTMLElement, cancelBtn: HTMLButtonElement, confirmBtn: HTMLButtonElement }}
 */
export function cardActions(cancelText, confirmText) {
  const el = document.createElement('div');
  el.className = 'popover-actions';
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'popover-secondary';
  cancelBtn.textContent = cancelText;
  const confirmBtn = confirmButton(confirmText);
  el.append(cancelBtn, confirmBtn);
  return { el, cancelBtn, confirmBtn };
}

/**
 * The header of both photo cards: the thumbnail (or a camera glyph without
 * one), the photo's fixed time, and where on the route this is.
 *
 * @param {{ thumbnailUrl: string|null, timestamp: Date }} photo
 * @param {string} whereText
 */
export function photoSummary({ thumbnailUrl, timestamp }, whereText) {
  const summary = document.createElement('div');
  summary.className = 'photo-card';
  let thumb;
  if (thumbnailUrl) {
    thumb = document.createElement('img');
    thumb.src = thumbnailUrl;
    thumb.alt = '';
  } else {
    thumb = document.createElement('span');
    thumb.textContent = '📷';
    thumb.setAttribute('aria-hidden', 'true');
  }
  thumb.className = 'photo-card-thumb';

  const text = document.createElement('div');
  text.append(popoverHeading(`Photo taken ${formatClockSeconds(timestamp)}`), popoverHint(whereText));

  summary.append(thumb, text);
  return summary;
}
