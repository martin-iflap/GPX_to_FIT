// The phone layout: the map fills the screen behind a "sheet" holding the
// sidebar, and the page's own scroll moves the sheet. The sheet starts at
// half the screen; scrolling it down shows up to MAX_MAP_SHARE of the map,
// and scrolling it up shows all the controls. The layout itself is CSS (see
// the 860px block in styles.css). This module does the three things CSS
// can't: open the page at the half-way point, move the profile panel into
// the sheet, and tell map.js how much of the map the sheet hides, so a
// fitted route or a panned-to pin lands in the part that's still visible.

/** Must match the phone breakpoint in styles.css. */
export const PHONE_LAYOUT_QUERY = '(max-width: 860px)';

// Share of the screen height the map shows when the page first opens.
const START_MAP_SHARE = 0.5;
// How long revealMap's smooth scroll is assumed to take. While it runs,
// coveredMapHeight answers for where the sheet is headed, not where it is.
const REVEAL_SETTLE_MS = 800;

let sidebar = null;
let mapPanel = null;
let revealUntil = 0;

/** @returns {boolean} whether the phone (scroll-sheet) layout is active */
export function isPhoneLayout() {
  return typeof window.matchMedia === 'function' && window.matchMedia(PHONE_LAYOUT_QUERY).matches;
}

/** The sheet's top edge in viewport pixels. */
function sheetTop() {
  return sidebar.getBoundingClientRect().top;
}

/** The scroll position at which the sheet's top sits at START_MAP_SHARE of the screen. */
function startScrollTop() {
  return Math.max(0, sheetTop() + window.scrollY - window.innerHeight * START_MAP_SHARE);
}

/**
 * How many pixels of the map, measured up from its bottom edge, the sheet
 * currently hides. Always 0 outside the phone layout.
 * @returns {number}
 */
export function coveredMapHeight() {
  if (!sidebar || !isPhoneLayout()) {
    return 0;
  }
  let top = sheetTop();
  if (Date.now() < revealUntil) {
    top = Math.max(top, window.innerHeight * START_MAP_SHARE);
  }
  const mapHeight = mapPanel.getBoundingClientRect().height;
  return Math.min(mapHeight, Math.max(0, mapHeight - Math.max(0, top)));
}

/**
 * On a phone, scrolls the sheet back down to the half-way point if it's
 * covering more of the map than that, so whatever just happened on the map
 * (a route loaded, a pin panned to) is actually in view. Does nothing when
 * the sheet is already that low, or outside the phone layout.
 */
export function revealMap() {
  if (!sidebar || !isPhoneLayout()) {
    return;
  }
  if (sheetTop() >= window.innerHeight * START_MAP_SHARE - 1) {
    return;
  }
  revealUntil = Date.now() + REVEAL_SETTLE_MS;
  window.scrollTo({ top: startScrollTop(), behavior: 'smooth' });
}

/**
 * Wires the phone layout. Call once at startup, after `mapModule.initMap`.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.sidebarEl - `#sidebar`, the sheet on phones
 * @param {HTMLElement} opts.mapPanelEl - `#mapPanel`, the profile panel's desktop home
 * @param {HTMLElement} opts.profilePanelEl - `#profilePanel`
 * @param {HTMLElement} opts.profileShowButtonEl - `#profileShowButton`
 * @param {{setAttributionPosition: (position: string) => void}} opts.mapModule
 */
export function initMobileLayout({ sidebarEl, mapPanelEl, profilePanelEl, profileShowButtonEl, mapModule }) {
  sidebar = sidebarEl;
  mapPanel = mapPanelEl;
  if (typeof window.matchMedia !== 'function') {
    return;
  }

  // Otherwise a reload restores the old scroll position, and the page opens
  // wherever the sheet was left rather than at the half-way point.
  if ('scrollRestoration' in history) {
    history.scrollRestoration = 'manual';
  }

  function apply(isPhone) {
    if (isPhone) {
      // At the top of the sheet the chart sits right under the visible map,
      // where its hover dot is. Over the map's bottom, as on desktop, the
      // sheet would hide it.
      sidebar.prepend(profilePanelEl, profileShowButtonEl);
      // The sheet hides the map's bottom corners, and the tile attribution
      // has to stay visible.
      mapModule.setAttributionPosition('topright');
      window.scrollTo(0, startScrollTop());
    } else {
      mapPanel.append(profilePanelEl, profileShowButtonEl);
      mapModule.setAttributionPosition('bottomright');
    }
  }

  const query = window.matchMedia(PHONE_LAYOUT_QUERY);
  apply(query.matches);
  query.addEventListener('change', (event) => apply(event.matches));
}
