import assert from 'node:assert/strict';
import { describe, it, mock } from 'node:test';

import { createPhotoPlacement, suggestCandidate } from '../../../src/gpx2fit/gui/js/placement/photoPlacement.js';

describe('createPhotoPlacement', () => {
  function photo(overrides = {}) {
    return {
      timestamp: new Date(2024, 5, 1, 9, 30, 15),
      onPlaced: mock.fn(),
      onCancel: mock.fn(),
      ...overrides,
    };
  }

  it('has nothing pending until a photo is armed', () => {
    const placement = createPhotoPlacement();

    assert.equal(placement.pending(), null);
    assert.equal(placement.consume(), null);
  });

  it('holds the armed photo until it is consumed, then clears it', () => {
    const placement = createPhotoPlacement();
    const armed = photo();
    placement.arm(armed);

    assert.equal(placement.pending(), armed);
    assert.equal(placement.consume(), armed);
    assert.equal(placement.pending(), null);
    assert.equal(placement.consume(), null);
  });

  it('does not call onCancel for a photo that was consumed', () => {
    const placement = createPhotoPlacement();
    const armed = photo();
    placement.arm(armed);
    placement.consume();
    placement.cancel();

    assert.equal(armed.onCancel.mock.callCount(), 0);
  });

  it('cancels the previous photo when another is armed, so only one is pending', () => {
    const placement = createPhotoPlacement();
    const first = photo();
    const second = photo();
    placement.arm(first);
    placement.arm(second);

    assert.equal(first.onCancel.mock.callCount(), 1);
    assert.equal(second.onCancel.mock.callCount(), 0);
    assert.equal(placement.pending(), second);
  });

  it('calls onCancel once and clears the pending photo on cancel', () => {
    const placement = createPhotoPlacement();
    const armed = photo();
    placement.arm(armed);
    placement.cancel();
    placement.cancel();

    assert.equal(armed.onCancel.mock.callCount(), 1);
    assert.equal(placement.pending(), null);
  });

  it('re-arming the same photo does not cancel it', () => {
    const placement = createPhotoPlacement();
    const armed = photo();
    placement.arm(armed);
    placement.arm(armed);

    assert.equal(armed.onCancel.mock.callCount(), 0);
    assert.equal(placement.pending(), armed);
  });

  it('tolerates a photo without an onCancel', () => {
    const placement = createPhotoPlacement();
    placement.arm({ timestamp: new Date(), onPlaced: () => {} });

    assert.doesNotThrow(() => placement.cancel());
  });
});


describe('createPhotoPlacement onChange', () => {
  function photo() {
    return { timestamp: new Date(2024, 5, 1, 9, 30, 15), onPlaced: () => {}, onCancel: mock.fn() };
  }

  function mountWithLog() {
    const seen = [];
    const placement = createPhotoPlacement({ onChange: (pending) => seen.push(pending) });
    return { placement, seen };
  }

  it('reports the photo when armed, and null once cancelled', () => {
    const { placement, seen } = mountWithLog();
    const armed = photo();
    placement.arm(armed);
    placement.cancel();

    assert.deepEqual(seen, [armed, null]);
  });

  it('reports null once the pending photo is consumed', () => {
    const { placement, seen } = mountWithLog();
    const armed = photo();
    placement.arm(armed);
    placement.consume();

    assert.deepEqual(seen, [armed, null]);
  });

  // The banner follows these calls, so it must end on the new photo.
  it('ends on the new photo when another replaces the pending one', () => {
    const { placement, seen } = mountWithLog();
    const first = photo();
    const second = photo();
    placement.arm(first);
    placement.arm(second);

    assert.equal(seen.at(-1), second);
    assert.equal(first.onCancel.mock.callCount(), 1);
  });

  it('stays quiet when nothing changes: cancel or consume with nothing pending, or re-arming the same photo', () => {
    const { placement, seen } = mountWithLog();
    placement.cancel();
    placement.consume();
    const armed = photo();
    placement.arm(armed);
    placement.arm(armed);

    assert.deepEqual(seen, [armed]);
  });

  it('runs the photo\'s onCancel before reporting null, so the row is idle by the time the banner hides', () => {
    const order = [];
    const placement = createPhotoPlacement({ onChange: (pending) => order.push(pending ? 'armed' : 'cleared') });
    placement.arm({ timestamp: new Date(), onPlaced: () => {}, onCancel: () => order.push('row idle') });
    placement.cancel();

    assert.deepEqual(order, ['armed', 'row idle', 'cleared']);
  });
});

describe('suggestCandidate', () => {
  const START = new Date(2024, 5, 1, 8, 0);
  // Uniform pace: one metre per second from 08:00.
  const estimate = (distance) => new Date(START.getTime() + distance * 1000);
  const passes = (...distances) => distances.map((d) => ({ distance_from_start: d }));

  it('picks the pass whose estimated arrival is nearest the photo\'s time', () => {
    const outAndBack = passes(1000, 5000);

    assert.equal(suggestCandidate(outAndBack, estimate(1200), estimate), 0);
    assert.equal(suggestCandidate(outAndBack, estimate(4700), estimate), 1);
  });

  it('compares both ways: a photo before an estimate counts as near too', () => {
    assert.equal(suggestCandidate(passes(1000, 3000, 5000), estimate(2900), estimate), 1);
    assert.equal(suggestCandidate(passes(1000, 3000, 5000), estimate(3100), estimate), 1);
  });

  it('takes the earlier pass on an exact tie', () => {
    assert.equal(suggestCandidate(passes(1000, 3000), estimate(2000), estimate), 0);
  });

  it('suggests nothing for a single pass: there is nothing to choose', () => {
    assert.equal(suggestCandidate(passes(1000), estimate(1000), estimate), null);
  });

  it('suggests nothing without an estimate (no valid start or duration)', () => {
    assert.equal(suggestCandidate(passes(1000, 5000), estimate(1000), () => null), null);
  });
});
