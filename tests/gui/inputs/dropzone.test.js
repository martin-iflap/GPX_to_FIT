import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createUploadLabel, wireDropzone, wireFileInput } from '../../../src/gpx2fit/gui/js/inputs/dropzone.js';

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

  it('says the user is about to upload on a hover, a focus or a drag over the box', () => {
    for (const type of ['pointerenter', 'focus', 'dragenter']) {
      const dropzoneEl = document.createElement('div');
      let intents = 0;
      wireDropzone(dropzoneEl, document.createElement('input'), () => {}, { onIntent: () => { intents += 1; } });
      dropzoneEl.dispatchEvent(new Event(type));
      assert.equal(intents, 1, type);
    }
  });
});

describe('createUploadLabel', () => {
  function setupLabel() {
    const dropzoneEl = document.createElement('div');
    const nameEl = document.createElement('p');
    const progressEl = document.createElement('p');
    nameEl.hidden = true;
    progressEl.hidden = true;
    const label = createUploadLabel({ dropzoneEl, nameEl, progressEl });
    const shown = () => ({
      name: nameEl.hidden ? null : nameEl.textContent,
      progress: progressEl.hidden ? null : progressEl.textContent,
      busy: dropzoneEl.classList.contains('is-busy'),
      ariaBusy: dropzoneEl.getAttribute('aria-busy'),
    });
    return { label, shown };
  }

  it('shows the chosen file and what it waits for at once, before it has parsed', () => {
    const { label, shown } = setupLabel();

    const upload = label.begin('alps.gpx');
    assert.deepEqual(shown(), { name: 'alps.gpx', progress: 'Waiting…', busy: true, ariaBusy: 'true' });
    upload.setStage('engine');
    assert.equal(shown().progress, 'Loading the converter…');
    upload.setStage('parse');
    assert.equal(shown().progress, 'Reading the route…');
  });

  it('keeps the name and drops the progress once the route is loaded', () => {
    const { label, shown } = setupLabel();

    label.begin('alps.gpx').succeed();

    assert.deepEqual(shown(), { name: 'alps.gpx', progress: null, busy: false, ariaBusy: null });
  });

  it('goes back to the route still loaded when a file fails, or to nothing on the first', () => {
    const { label, shown } = setupLabel();

    label.begin('broken.gpx').fail();
    assert.deepEqual(shown(), { name: null, progress: null, busy: false, ariaBusy: null });

    label.begin('alps.gpx').succeed();
    label.begin('broken.gpx').fail();
    assert.deepEqual(shown(), { name: 'alps.gpx', progress: null, busy: false, ariaBusy: null });
  });

  it('ignores a failure reported after the same upload succeeded, even with another one loading', () => {
    const { label, shown } = setupLabel();

    const upload = label.begin('alps.gpx');
    upload.succeed();
    const next = label.begin('dolomites.gpx');
    // main.js's catch runs if a step after the parse (drawing the route) throws.
    upload.fail();
    assert.deepEqual(shown(), { name: 'dolomites.gpx', progress: 'Waiting…', busy: true, ariaBusy: 'true' });

    next.fail();
    assert.equal(shown().name, 'alps.gpx');
  });

  it('keeps showing the newest file while an earlier one finishes ahead of it', () => {
    const { label, shown } = setupLabel();

    const first = label.begin('alps.gpx');
    const second = label.begin('dolomites.gpx');
    second.setStage('engine');
    first.setStage('parse');
    assert.deepEqual(shown(), { name: 'dolomites.gpx', progress: 'Loading the converter…', busy: true, ariaBusy: 'true' });
    first.succeed();
    assert.deepEqual(shown(), { name: 'dolomites.gpx', progress: 'Loading the converter…', busy: true, ariaBusy: 'true' });

    // The first did load, so it's the route left when the second fails.
    second.fail();
    assert.deepEqual(shown(), { name: 'alps.gpx', progress: null, busy: false, ariaBusy: null });
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
