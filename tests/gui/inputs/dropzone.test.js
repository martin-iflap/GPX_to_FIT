import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { wireDropzone, wireFileInput } from '../../../src/gpx2fit/gui/js/inputs/dropzone.js';

function setup() {
  const dropzoneEl = document.createElement('div');
  const inputEl = document.createElement('input');
  inputEl.type = 'file';
  const received = [];
  let pickerOpened = 0;
  inputEl.click = () => {
    pickerOpened += 1;
  };
  wireDropzone(dropzoneEl, inputEl, (files) => received.push(files));
  return { dropzoneEl, inputEl, received, opened: () => pickerOpened };
}

function dragEvent(type, files) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  if (files) {
    Object.defineProperty(event, 'dataTransfer', { value: { files } });
  }
  return event;
}

function choose(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true });
  input.dispatchEvent(new Event('change'));
}

const gpx = () => new File(['<gpx/>'], 'route.gpx');

describe('wireDropzone', () => {
  it('opens the file picker on a click, Enter or Space', () => {
    const { dropzoneEl, opened } = setup();
    dropzoneEl.click();
    for (const key of ['Enter', ' ']) {
      const event = new KeyboardEvent('keydown', { key, cancelable: true });
      dropzoneEl.dispatchEvent(event);
      assert.equal(event.defaultPrevented, true);
    }
    dropzoneEl.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    assert.equal(opened(), 3);
  });

  it('marks the box while dragging over it, and accepts the drop', () => {
    const { dropzoneEl } = setup();
    const over = dragEvent('dragover');
    dropzoneEl.dispatchEvent(over);
    assert.equal(over.defaultPrevented, true);
    assert.ok(dropzoneEl.classList.contains('is-dragover'));
    dropzoneEl.dispatchEvent(dragEvent('dragleave'));
    assert.ok(!dropzoneEl.classList.contains('is-dragover'));
  });

  it('hands the dropped files over as an array', () => {
    const { dropzoneEl, received } = setup();
    const file = gpx();
    const drop = dragEvent('drop', [file]);
    dropzoneEl.dispatchEvent(drop);
    assert.equal(drop.defaultPrevented, true);
    assert.ok(!dropzoneEl.classList.contains('is-dragover'));
    assert.deepEqual(received, [[file]]);
  });

  it('hands the chosen files over', () => {
    const { inputEl, received } = setup();
    const file = gpx();
    choose(inputEl, [file]);
    assert.deepEqual(received, [[file]]);
  });
});

describe('wireFileInput', () => {
  it('copies the files out before clearing the input', () => {
    const input = document.createElement('input');
    input.type = 'file';
    const received = [];
    wireFileInput(input, (files) => received.push(files));
    const file = gpx();
    // Like a browser, clearing the value empties the list it hands out.
    let files = [file];
    Object.defineProperty(input, 'files', { get: () => files, configurable: true });
    Object.defineProperty(input, 'value', {
      get: () => '',
      set: () => {
        files = [];
      },
      configurable: true,
    });
    input.dispatchEvent(new Event('change'));
    assert.deepEqual(received, [[file]]);
    assert.deepEqual(files, []);
  });
});
