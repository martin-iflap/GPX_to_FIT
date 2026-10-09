import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import './testUtils/domSetup.js';
import { moveFocusIndex } from '../../src/gpx2fit/gui/js/devicePicker.js';

// The picker's shape in miniature: "No device" alone (0), a brand of three
// (1 2 / 3), then a brand of two (4 5).
const ROWS = [1, 2, 1, 2];

describe('moveFocusIndex', () => {
  it('steps left and right in reading order, across rows and brands', () => {
    assert.equal(moveFocusIndex(ROWS, 2, 'ArrowRight'), 3);
    assert.equal(moveFocusIndex(ROWS, 3, 'ArrowRight'), 4);
    assert.equal(moveFocusIndex(ROWS, 1, 'ArrowLeft'), 0);
  });

  it('keeps the column going up and down', () => {
    assert.equal(moveFocusIndex(ROWS, 2, 'ArrowDown'), 3);
    assert.equal(moveFocusIndex(ROWS, 3, 'ArrowDown'), 4);
    assert.equal(moveFocusIndex(ROWS, 5, 'ArrowUp'), 3);
    assert.equal(moveFocusIndex(ROWS, 1, 'ArrowUp'), 0);
  });

  it("takes a shorter row's last option rather than skipping it", () => {
    // Right column of the first brand, down into its single-option row.
    assert.equal(moveFocusIndex(ROWS, 2, 'ArrowDown'), 3);
    // And from "No device" down, the left column.
    assert.equal(moveFocusIndex(ROWS, 0, 'ArrowDown'), 1);
  });

  it('stops at the edges instead of wrapping', () => {
    assert.equal(moveFocusIndex(ROWS, 0, 'ArrowLeft'), 0);
    assert.equal(moveFocusIndex(ROWS, 0, 'ArrowUp'), 0);
    assert.equal(moveFocusIndex(ROWS, 5, 'ArrowRight'), 5);
    assert.equal(moveFocusIndex(ROWS, 4, 'ArrowDown'), 4);
  });

  it('jumps to the first and last option on Home and End', () => {
    assert.equal(moveFocusIndex(ROWS, 3, 'Home'), 0);
    assert.equal(moveFocusIndex(ROWS, 3, 'End'), 5);
  });

  it('leaves any other key where it is', () => {
    assert.equal(moveFocusIndex(ROWS, 3, 'Enter'), 3);
  });
});
