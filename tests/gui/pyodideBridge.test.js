import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { classifyPyError } from '../../src/gpx2fit/gui/js/pyodideBridge.js';

describe('classifyPyError', () => {
  it('extracts the message and tags isInputError for a traceback ending in InputError', () => {
    const pyError = new Error(
      'Traceback (most recent call last):\n  File "combine.py", line 42, in _pace_segment\nInputError: Anchor at 4.2km is earlier than the anchor before it.',
    );
    const result = classifyPyError(pyError);
    assert.equal(result.isInputError, true);
    assert.equal(result.name, 'InputError');
    assert.equal(result.message, 'Anchor at 4.2km is earlier than the anchor before it.');
  });

  it('handles trailing blank lines after the InputError line', () => {
    const pyError = new Error('InputError: Two anchors resolve to the same point.\n\n  \n');
    const result = classifyPyError(pyError);
    assert.equal(result.isInputError, true);
    assert.equal(result.message, 'Two anchors resolve to the same point.');
  });

  it('passes through a traceback ending in a different exception class unchanged', () => {
    const pyError = new Error(
      'Traceback (most recent call last):\n  File "combine.py", line 10, in combine\nValueError: missing sport',
    );
    const result = classifyPyError(pyError);
    assert.equal(result, pyError);
    assert.equal(result.isInputError, undefined);
  });

  it('passes through a non-Error value unchanged', () => {
    const value = 'some non-Error thrown value';
    assert.equal(classifyPyError(value), value);
  });

  it('passes through an error with an empty message unchanged', () => {
    const pyError = new Error('');
    assert.equal(classifyPyError(pyError), pyError);
  });
});
