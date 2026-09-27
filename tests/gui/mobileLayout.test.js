import './testUtils/domSetup.js';

import assert from 'node:assert/strict';
import { beforeEach, describe, it, mock } from 'node:test';

import {
  coveredMapHeight,
  initMobileLayout,
  isPhoneLayout,
  PHONE_LAYOUT_QUERY,
  revealMap,
} from '../../src/gpx2fit/gui/js/mobileLayout.js';

// jsdom has no layout, so the sheet's position and the map's height are
// stubbed, and matchMedia is a fake whose `matches` a test flips by hand.
let query;
let els;
let mapModule;
let scrollCalls;

function fakeMediaQuery(matches) {
  const listeners = [];
  return {
    matches,
    addEventListener: (_type, fn) => listeners.push(fn),
    set(next) {
      this.matches = next;
      listeners.forEach((fn) => fn({ matches: next }));
    },
  };
}

function setSheetTop(top) {
  els.sidebarEl.getBoundingClientRect = () => ({ top });
}

beforeEach(() => {
  document.body.innerHTML = `
    <div id="mapPanel"><div id="map"></div><section id="profilePanel"></section><button id="profileShowButton"></button></div>
    <aside id="sidebar"><header class="sidebar-header"></header></aside>`;
  els = {
    mapPanelEl: document.getElementById('mapPanel'),
    sidebarEl: document.getElementById('sidebar'),
    profilePanelEl: document.getElementById('profilePanel'),
    profileShowButtonEl: document.getElementById('profileShowButton'),
  };
  els.mapPanelEl.getBoundingClientRect = () => ({ height: 1000 });
  setSheetTop(0.85 * window.innerHeight);

  query = fakeMediaQuery(true);
  window.matchMedia = (q) => {
    assert.equal(q, PHONE_LAYOUT_QUERY);
    return query;
  };
  scrollCalls = [];
  window.scrollTo = (...args) => scrollCalls.push(args);
  mapModule = { setAttributionPosition: mock.fn() };
});

describe('initMobileLayout', () => {
  it('moves the profile panel into the top of the sheet and opens at the half-way point', () => {
    initMobileLayout({ ...els, mapModule });

    assert.equal(els.sidebarEl.firstElementChild, els.profilePanelEl);
    assert.equal(els.profilePanelEl.nextElementSibling, els.profileShowButtonEl);
    assert.equal(mapModule.setAttributionPosition.mock.calls.at(-1).arguments[0], 'topright');
    // The sheet starts at 85% of the screen, so the half-way point is 35% down.
    const [x, y] = scrollCalls.at(-1);
    assert.equal(x, 0);
    assert.ok(Math.abs(y - 0.35 * window.innerHeight) < 1e-6);
  });

  it('puts everything back when the screen widens past the phone layout', () => {
    initMobileLayout({ ...els, mapModule });
    query.set(false);

    assert.equal(els.profilePanelEl.parentElement, els.mapPanelEl);
    assert.equal(els.mapPanelEl.lastElementChild, els.profileShowButtonEl);
    assert.equal(mapModule.setAttributionPosition.mock.calls.at(-1).arguments[0], 'bottomright');
    assert.equal(isPhoneLayout(), false);
  });
});

describe('coveredMapHeight', () => {
  it('is how much of the map sits below the sheet top', () => {
    initMobileLayout({ ...els, mapModule });
    setSheetTop(600);
    assert.equal(coveredMapHeight(), 400);
    // Scrolled all the way up, the sheet covers the whole map.
    setSheetTop(-50);
    assert.equal(coveredMapHeight(), 1000);
  });

  it('is zero outside the phone layout', () => {
    initMobileLayout({ ...els, mapModule });
    query.set(false);
    setSheetTop(600);
    assert.equal(coveredMapHeight(), 0);
  });
});

describe('revealMap', () => {
  it('scrolls back to the half-way point only when the sheet covers more than half', () => {
    initMobileLayout({ ...els, mapModule });
    scrollCalls = [];

    setSheetTop(0.6 * window.innerHeight);
    revealMap();
    assert.equal(scrollCalls.length, 0);

    setSheetTop(100);
    revealMap();
    assert.equal(scrollCalls.length, 1);
    assert.equal(scrollCalls[0][0].behavior, 'smooth');
    // Mid-scroll, the inset already answers for where the sheet is headed.
    assert.equal(coveredMapHeight(), 1000 - 0.5 * window.innerHeight);
  });
});
