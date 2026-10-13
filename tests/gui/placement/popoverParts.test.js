import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import {
  cardActions,
  confirmButton,
  estimateHint,
  optionButton,
  photoSummary,
  popoverHeading,
  popoverHint,
} from '../../../src/gpx2fit/gui/js/placement/popoverParts.js';

describe('popover parts', () => {
  it('builds a heading', () => {
    const heading = popoverHeading('10.00 km');

    assert.equal(heading.tagName, 'P');
    assert.equal(heading.className, 'popover-heading');
    assert.equal(heading.textContent, '10.00 km');
  });

  it('builds a hint, and a warning one', () => {
    const hint = popoverHint('Pick a pass.');
    assert.equal(hint.className, 'popover-hint');
    assert.equal(hint.textContent, 'Pick a pass.');
    assert.equal(hint.hidden, false);

    const warning = popoverHint('Not here.', { warning: true, hidden: true });
    assert.equal(warning.className, 'popover-hint is-warning');
    assert.equal(warning.hidden, true);
  });

  it('says a pre-filled time is only an estimate, when there is one', () => {
    assert.equal(estimateHint(new Date(2024, 5, 1, 12, 0)).textContent, 'Pre-filled time is an estimate — check and adjust.');
    assert.equal(estimateHint(new Date(2024, 5, 1, 12, 0)).className, 'popover-hint');
    assert.equal(estimateHint(null), null);
  });

  it('builds the confirm button', () => {
    const button = confirmButton('Add stop');

    assert.equal(button.type, 'button');
    assert.equal(button.className, 'primary-button popover-confirm');
    assert.equal(button.textContent, 'Add stop');
  });

  it('builds an option with a title and a detail, as text', () => {
    const option = optionButton('Add <anchor>', 'A single known timestamp');

    assert.equal(option.type, 'button');
    assert.equal(option.className, 'candidate-option');
    assert.equal(option.querySelector('.candidate-distance').textContent, 'Add <anchor>');
    assert.equal(option.querySelector('.candidate-estimate').textContent, 'A single known timestamp');
    // Text, never markup.
    assert.equal(option.querySelector('anchor'), null);
  });

  it('builds a card\'s way out and its confirm, in that order', () => {
    const { el, cancelBtn, confirmBtn } = cardActions('Not here', 'Place photo here');

    assert.equal(el.className, 'popover-actions');
    assert.deepEqual([...el.children], [cancelBtn, confirmBtn]);
    assert.equal(cancelBtn.className, 'popover-secondary');
    assert.equal(cancelBtn.type, 'button');
    assert.equal(cancelBtn.textContent, 'Not here');
    assert.equal(confirmBtn.className, 'primary-button popover-confirm');
    assert.equal(confirmBtn.textContent, 'Place photo here');
  });

  describe('photoSummary', () => {
    const timestamp = new Date(2024, 5, 1, 11, 5, 30);

    it('shows the thumbnail, the time to the second and where', () => {
      const summary = photoSummary({ thumbnailUrl: 'blob:thumb', timestamp }, 'At 4.20 km along the route');

      assert.equal(summary.className, 'photo-card');
      const thumb = summary.querySelector('.photo-card-thumb');
      assert.equal(thumb.tagName, 'IMG');
      assert.equal(thumb.getAttribute('src'), 'blob:thumb');
      assert.equal(thumb.alt, '');
      assert.equal(summary.querySelector('.popover-heading').textContent, 'Photo taken 11:05:30');
      assert.equal(summary.querySelector('.popover-hint').textContent, 'At 4.20 km along the route');
    });

    it('shows a camera glyph, hidden from screen readers, without a thumbnail', () => {
      const summary = photoSummary({ thumbnailUrl: null, timestamp }, 'Somewhere');

      const thumb = summary.querySelector('.photo-card-thumb');
      assert.equal(thumb.tagName, 'SPAN');
      assert.equal(thumb.textContent, '📷');
      assert.equal(thumb.getAttribute('aria-hidden'), 'true');
    });
  });
});
