// The popover host: a Leaflet popup on a desktop (map.openPopup, faked here)
// and #routeDialog on a phone. jsdom has no showModal/close, so the dialog
// gets a stand-in that behaves like a browser's where it matters: the close
// event comes a task after close(), and showModal on an open dialog throws.

import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';

import '../testUtils/domSetup.js';
import { createPopoverHost } from '../../../src/gpx2fit/gui/js/placement/popoverHost.js';

/** A task later, by when a dialog's queued close event has fired. */
function nextTask() {
  return new Promise((resolve) => window.setTimeout(resolve, 0));
}

/** #routeDialog's markup from index.html, with a browser-like showModal/close. */
function createDialog() {
  const dialog = document.createElement('dialog');
  dialog.innerHTML = `
    <div class="modal-panel">
      <button type="button" data-route-close>×</button>
      <div class="route-dialog-body"></div>
    </div>`;
  dialog.showModalCalls = 0;
  dialog.showModal = () => {
    if (dialog.open) {
      throw new DOMException('The dialog is already open.', 'InvalidStateError');
    }
    dialog.showModalCalls += 1;
    dialog.setAttribute('open', '');
  };
  dialog.close = () => {
    if (!dialog.open) {
      return;
    }
    dialog.removeAttribute('open');
    window.setTimeout(() => dialog.dispatchEvent(new Event('close')), 0);
  };
  document.body.append(dialog);
  return dialog;
}

function mount({ phone = true } = {}) {
  const dialog = createDialog();
  const mapCalls = [];
  const layout = { phone };
  const host = createPopoverHost({
    map: { openPopup: (...args) => mapCalls.push(args) },
    dialogEl: dialog,
    isPhoneLayout: () => layout.phone,
  });
  const body = () => dialog.querySelector('.route-dialog-body');
  return { host, dialog, mapCalls, layout, body };
}

/** Opens content that records what it was given and how often its onClose ran. */
function openContent(host, text) {
  const record = { closes: 0, close: null, updateLayout: null };
  host.open(1, 2, (container, close, updateLayout) => {
    record.close = close;
    record.updateLayout = updateLayout;
    container.append(Object.assign(document.createElement('p'), { textContent: text }));
  }, { onClose: () => { record.closes += 1; } });
  return record;
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('createPopoverHost on a desktop', () => {
  it('hands the content to the map\'s popup, as it was given', () => {
    const { host, dialog, mapCalls } = mount({ phone: false });
    const build = () => {};
    const onClose = () => {};
    host.open(46.5, 8.1, build, { onClose });

    assert.equal(mapCalls.length, 1);
    const [lat, lon, passedBuild, opts] = mapCalls[0];
    assert.deepEqual([lat, lon], [46.5, 8.1]);
    assert.equal(passedBuild, build);
    assert.equal(opts.onClose, onClose);
    assert.equal(dialog.open, false);
  });

  it('asks which layout is active on every open, since a phone can rotate past the breakpoint', () => {
    const { host, dialog, mapCalls, layout } = mount({ phone: false });
    openContent(host, 'first');
    layout.phone = true;
    openContent(host, 'second');

    assert.equal(mapCalls.length, 1);
    assert.equal(dialog.open, true);
  });
});

describe('createPopoverHost on a phone', () => {
  it('builds the content into the dialog body and opens it as a modal', () => {
    const { host, dialog, mapCalls, body } = mount();
    const record = openContent(host, 'Add anchor');

    assert.equal(mapCalls.length, 0);
    assert.equal(dialog.open, true);
    assert.equal(body().textContent, 'Add anchor');
    assert.equal(typeof record.close, 'function');
    assert.doesNotThrow(() => record.updateLayout());
  });

  it('the content\'s own close() closes the dialog and runs onClose once', async () => {
    const { host, dialog } = mount();
    const record = openContent(host, 'Add anchor');
    record.close();
    assert.equal(dialog.open, false);
    assert.equal(record.closes, 1);

    await nextTask();
    assert.equal(record.closes, 1);
  });

  it('the × button closes it, and onClose runs once the dialog has closed', async () => {
    const { host, dialog } = mount();
    const record = openContent(host, 'Add anchor');
    dialog.querySelector('[data-route-close]').click();
    assert.equal(dialog.open, false);

    await nextTask();
    assert.equal(record.closes, 1);
  });

  it('a click on the backdrop closes it, a click inside the panel does not', async () => {
    const { host, dialog, body } = mount();
    const record = openContent(host, 'Add anchor');
    body().firstElementChild.click();
    await nextTask();
    assert.equal(dialog.open, true);
    assert.equal(record.closes, 0);

    dialog.click();
    await nextTask();
    assert.equal(dialog.open, false);
    assert.equal(record.closes, 1);
  });

  it('runs onClose when the browser closes the dialog itself (Esc)', async () => {
    const { host, dialog } = mount();
    const record = openContent(host, 'Add anchor');
    dialog.close();
    await nextTask();

    assert.equal(record.closes, 1);
  });

  it('new content replacing open content ends the old content only, and keeps the dialog open', async () => {
    const { host, dialog, body } = mount();
    const first = openContent(host, 'Which pass?');
    const second = openContent(host, 'Place photo here');

    assert.equal(first.closes, 1);
    assert.equal(second.closes, 0);
    assert.equal(dialog.open, true);
    assert.equal(dialog.showModalCalls, 1);
    assert.equal(body().textContent, 'Place photo here');

    await nextTask();
    assert.equal(second.closes, 0);
  });

  it('content that closes itself to open the next step leaves the next step open (the pass picker, then a card)', async () => {
    const { host, dialog, body } = mount();
    let second = null;
    const first = { closes: 0 };
    host.open(1, 2, (container, close) => {
      const pick = document.createElement('button');
      pick.addEventListener('click', () => {
        close();
        second = openContent(host, 'Place photo here');
      });
      container.append(pick);
    }, { onClose: () => { first.closes += 1; } });

    body().querySelector('button').click();
    // The first close()'s event arrives now, after the card has opened.
    await nextTask();

    assert.equal(first.closes, 1);
    assert.equal(second.closes, 0);
    assert.equal(dialog.open, true);
    assert.equal(body().textContent, 'Place photo here');
  });

  it('works without an onClose', async () => {
    const { host, dialog } = mount();
    let close = null;
    host.open(1, 2, (container, closeContent) => {
      close = closeContent;
    });
    close();
    await nextTask();
    assert.equal(dialog.open, false);

    // And the next content still opens.
    openContent(host, 'Add anchor');
    assert.equal(dialog.open, true);
  });
});
