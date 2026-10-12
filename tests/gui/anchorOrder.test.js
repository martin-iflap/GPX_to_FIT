import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  describeOrderConflict,
  describeOrderConflictBriefly,
  findOrderConflict,
  timedPoints,
} from '../../src/gpx2fit/gui/js/anchorOrder.js';

const at = (hours, minutes, seconds = 0) => new Date(2024, 5, 1, hours, minutes, seconds);

function pointAt(distanceFromStart, earliest, latest = earliest, kind = 'photo') {
  return { kind, distanceFromStart, earliest, latest };
}

describe('findOrderConflict', () => {
  // A photo at 1 km taken at 10:00, another at 3 km taken at 11:00.
  const photos = [pointAt(1000, at(10, 0)), pointAt(3000, at(11, 0))];

  it('finds nothing with no other points', () => {
    assert.equal(findOrderConflict([], { distanceFromStart: 500, time: at(9, 0) }), null);
  });

  it('finds nothing for a time between its neighbours', () => {
    assert.equal(findOrderConflict(photos, { distanceFromStart: 2000, time: at(10, 30) }), null);
    assert.equal(findOrderConflict(photos, { distanceFromStart: 500, time: at(9, 0) }), null);
    assert.equal(findOrderConflict(photos, { distanceFromStart: 4000, time: at(12, 0) }), null);
  });

  it('refuses a point past one taken later: it must go before that one', () => {
    const conflict = findOrderConflict(photos, { distanceFromStart: 3500, time: at(10, 30) });

    assert.deepEqual(conflict, { point: photos[1], issue: 'mustGoBefore' });
  });

  it('refuses a point short of one taken earlier: it must go after that one', () => {
    const conflict = findOrderConflict(photos, { distanceFromStart: 500, time: at(10, 30) });

    assert.deepEqual(conflict, { point: photos[0], issue: 'mustGoAfter' });
  });

  it('refuses the same time anywhere else, since no time passes in between', () => {
    // Neither side of the other point would do, so it says neither.
    for (const distanceFromStart of [500, 2000, 3500]) {
      assert.deepEqual(
        findOrderConflict(photos, { distanceFromStart, time: at(11, 0) }),
        { point: photos[1], issue: 'sameTime' },
      );
    }
  });

  it('refuses the very point another one is on, whatever the time', () => {
    const conflict = findOrderConflict(photos, { distanceFromStart: 3000, time: at(12, 0) });

    assert.deepEqual(conflict, { point: photos[1], issue: 'samePoint' });
  });

  it('names the limit when several are in the way going forward: the first one passed', () => {
    // Taken at 9:30, before both: placed at 4 km it's past both, and the
    // furthest it may go is short of the one at 1 km.
    const conflict = findOrderConflict([...photos].reverse(), { distanceFromStart: 4000, time: at(9, 30) });

    assert.equal(conflict.point, photos[0]);
    assert.equal(conflict.issue, 'mustGoBefore');
  });

  it('names the limit when several are in the way going back: the last one passed', () => {
    const conflict = findOrderConflict(photos, { distanceFromStart: 500, time: at(11, 30) });

    assert.equal(conflict.point, photos[1]);
    assert.equal(conflict.issue, 'mustGoAfter');
  });

  describe('against a stop, which spans its arrival to its departure', () => {
    const stop = pointAt(2000, at(10, 0), at(10, 20), 'stop');

    it('refuses its arrival or departure time anywhere else', () => {
      for (const time of [at(10, 0), at(10, 20)]) {
        assert.deepEqual(
          findOrderConflict([stop], { distanceFromStart: 2500, time }),
          { point: stop, issue: 'sameTime' },
        );
      }
    });

    it('allows a point after the stop only once it has left', () => {
      assert.equal(findOrderConflict([stop], { distanceFromStart: 2500, time: at(10, 21) }), null);
      assert.deepEqual(
        findOrderConflict([stop], { distanceFromStart: 2500, time: at(10, 10) }),
        { point: stop, issue: 'mustGoBefore' },
      );
    });

    it('allows a point before the stop only until it arrives', () => {
      assert.equal(findOrderConflict([stop], { distanceFromStart: 1500, time: at(9, 59) }), null);
      assert.deepEqual(
        findOrderConflict([stop], { distanceFromStart: 1500, time: at(10, 10) }),
        { point: stop, issue: 'mustGoAfter' },
      );
    });
  });
});

describe('timedPoints', () => {
  const anchors = [
    { id: 1, distanceFromStart: 1000, timestamp: at(10, 0), source: 'photo' },
    { id: 2, distanceFromStart: 2000, timestamp: at(10, 30), source: 'user' },
  ];
  const stops = [
    { id: 1, distanceFromStart: 1500, mode: 'startEnd', arrival: at(10, 10), departure: at(10, 20) },
    { id: 2, distanceFromStart: 2500, mode: 'duration', durationSeconds: 600 },
  ];

  it('takes each anchor at its time, a photo as a photo and the rest as anchors', () => {
    assert.deepEqual(timedPoints(anchors, []), [
      pointAt(1000, at(10, 0), at(10, 0), 'photo'),
      pointAt(2000, at(10, 30), at(10, 30), 'anchor'),
    ]);
  });

  it('takes a start/end stop over its stay, and skips a duration-only one, which has no clock time', () => {
    assert.deepEqual(timedPoints([], stops), [pointAt(1500, at(10, 10), at(10, 20), 'stop')]);
  });

  it('leaves out the anchor being moved', () => {
    assert.deepEqual(
      timedPoints(anchors, stops, { excludeAnchorId: 1 }).map((p) => p.distanceFromStart),
      [2000, 1500],
    );
  });
});

describe('describing a conflict', () => {
  const later = { point: pointAt(3000, at(11, 0)), issue: 'mustGoBefore' };
  const earlier = { point: pointAt(1000, at(10, 0), at(10, 0), 'anchor'), issue: 'mustGoAfter' };
  const stop = { point: pointAt(2000, at(10, 0), at(10, 20), 'stop'), issue: 'mustGoBefore' };
  const same = { point: pointAt(3000, at(11, 0)), issue: 'samePoint' };
  const sameTime = { point: pointAt(3000, at(11, 0)), issue: 'sameTime' };

  it('says what is in the way, its time, and which side to go', () => {
    assert.equal(
      describeOrderConflict(later, at(10, 30)),
      'This photo was taken at 10:30:00, but the photo at 3.00 km is at 11:00:00. '
        + 'Points on the route have to follow each other in time, so place this one before 3.00 km.',
    );
    assert.equal(
      describeOrderConflict(earlier, at(9, 30)),
      'This photo was taken at 09:30:00, but the anchor at 1.00 km is at 10:00:00. '
        + 'Points on the route have to follow each other in time, so place this one after 1.00 km.',
    );
  });

  it('gives a stop its whole stay', () => {
    assert.match(describeOrderConflict(stop, at(10, 10)), /the stop at 2\.00 km is at 10:00:00–10:20:00\./);
  });

  it('says a point that is taken is taken', () => {
    assert.equal(
      describeOrderConflict(same, at(12, 0)),
      'The photo at 3.00 km is already on this point. Place this one a little before or after it.',
    );
  });

  it('says two points can\'t share a time', () => {
    assert.equal(
      describeOrderConflict(sameTime, at(11, 0)),
      'This photo was taken at 11:00:00, the same time as the photo at 3.00 km. Two points on the route can\'t share a time.',
    );
  });

  it('has a short form for a pass that can\'t be picked', () => {
    assert.equal(describeOrderConflictBriefly(later), 'Out of time order with the photo at 3.00 km');
    assert.equal(describeOrderConflictBriefly(same), 'The photo at 3.00 km is already here');
    assert.equal(describeOrderConflictBriefly(sameTime), 'Same time as the photo at 3.00 km');
  });
});
