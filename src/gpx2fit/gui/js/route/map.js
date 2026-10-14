// Leaflet map: theme-aware basemap, route polyline rendering, layers of
// numbered pins (one per list, see createPinLayer), and the popup the
// placement popovers open in on a desktop (popoverHost.js decides). Nothing
// here knows about anchors, stops or the phone layout. Route-click detection
// is attached to the polyline layer itself (not the map), so only clicks on
// the route line place an anchor.
//
// Both light and dark now come from Thunderforest, so basemap look stays
// consistent across the theme toggle — free (Hobby Project tier, 150k
// tiles/month) with an API key from https://www.thunderforest.com/, kept
// in the gitignored config.js (see config.example.js). Light uses
// "Outdoors" for real trail/contour detail; dark uses "Transport Dark",
// Thunderforest's only dark-optimized style — note it's tuned for
// roads/transit rather than trails, so it shows less trail detail than
// Outdoors does.

/* global L */
// Leaflet is loaded globally via the <script> tag in index.html (no bundler,
// no npm install for it) — this directive just tells the IDE/linter that `L`
// is an intentional external global, not a typo.

import { THUNDERFOREST_API_KEY } from '../config.js';
import { accentColor } from '../util/cssVars.js';

const THUNDERFOREST_ATTRIBUTION =
  'Maps &copy; <a href="https://www.thunderforest.com/">Thunderforest</a>, Data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

const TILE_LAYERS = {
  light: {
    url: `https://{s}.tile.thunderforest.com/outdoors/{z}/{x}/{y}{r}.png?apikey=${THUNDERFOREST_API_KEY}`,
    attribution: THUNDERFOREST_ATTRIBUTION,
    subdomains: ['a', 'b', 'c'],
    maxNativeZoom: 18,
  },
  dark: {
    url: `https://{s}.tile.thunderforest.com/transport-dark/{z}/{x}/{y}{r}.png?apikey=${THUNDERFOREST_API_KEY}`,
    attribution: THUNDERFOREST_ATTRIBUTION,
    subdomains: ['a', 'b', 'c'],
    maxNativeZoom: 18,
  },
};

// Leaflet's objects are `any`, like `L` itself (see types/cdn-globals.d.ts).
/** @type {any} */
let map = null;
/** @type {any} */
let tileLayer = null;
/** @type {any} */
let routeLine = null;
/** @type {any} */
let routeHitLine = null;
/** @type {any} */
let hoverMarker = null;
// How many pixels at the bottom of the map something else covers (the phone
// layout's sheet). See setBottomInsetProvider.
let getBottomInset = () => 0;
// Set while a photo waits to be placed (setPlacementMode): the route then
// shows where a tap would land.
let placementMode = false;

// Screen-pixel width of the invisible click target drawn on top of the
// visible route line — the visible stroke (weight 4) is too thin to click
// reliably, so a much fatter, near-transparent line underneath it (opacity
// kept just above 0 rather than exactly 0, since a hard 0 risks the
// renderer treating the stroke as non-hit-testable) absorbs the click.
// A fingertip is far less precise than a mouse, so touch screens get more.
const ROUTE_HIT_WEIGHT = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches ? 34 : 22;

/**
 * Creates the Leaflet map instance in the given container. Must be called
 * once before any other function in this module.
 *
 * @param {string} containerId - id of the element to render the map into
 * @returns {L.Map}
 */
export function initMap(containerId) {
  map = L.map(containerId, { zoomControl: true, attributionControl: true });
  map.setView([46.8, 8.2], 8);
  // Observes the container itself rather than the window, so any layout
  // change that resizes the map (not just a window resize) keeps Leaflet in
  // step. pan: false keeps the top-left anchored, so the view doesn't drift.
  new ResizeObserver(() => map.invalidateSize({ pan: false })).observe(map.getContainer());
  return map;
}

/**
 * Registers how many pixels at the bottom of the map are hidden behind
 * something (the phone layout's sheet — see mobileLayout.js), so fitting the
 * route and panning to a pin aim at the part of the map that's visible.
 * @param {() => number} provider
 */
export function setBottomInsetProvider(provider) {
  getBottomInset = provider;
}

/** Moves the tile attribution control, e.g. out from under the phone layout's sheet. @param {'topright'|'bottomright'} position */
export function setAttributionPosition(position) {
  map.attributionControl?.setPosition(position);
}

/**
 * Restyles the map for a theme: swaps the basemap tiles, and recolors the
 * route line with the theme's accent. Call it once `data-theme` is set, so
 * the accent read is the new theme's. (The hover dot re-reads it on every show.)
 * @param {'light'|'dark'} theme
 */
export function setTheme(theme) {
  const config = theme === 'dark' ? TILE_LAYERS.dark : TILE_LAYERS.light;
  if (tileLayer) {
    map.removeLayer(tileLayer);
  }
  tileLayer = L.tileLayer(config.url, {
    attribution: config.attribution,
    subdomains: config.subdomains,
    maxZoom: 19,
    maxNativeZoom: config.maxNativeZoom,
    detectRetina: true,
  });
  tileLayer.addTo(map);
  routeLine?.setStyle({ color: accentColor() });
}

/**
 * Draws (or redraws) the route polyline plus its invisible fat click-target
 * line, and fits the map view to it.
 *
 * @param {{lat: number, lon: number}[]} points - route points in order
 * @param {(lat: number, lon: number) => void} onRouteClick - called with the
 *   clicked lat/lon whenever the user clicks anywhere on the route line
 */
export function renderRoute(points, onRouteClick) {
  if (routeLine) {
    map.removeLayer(routeLine);
    routeLine = null;
  }
  if (routeHitLine) {
    map.removeLayer(routeHitLine);
    routeHitLine = null;
  }

  const latLngs = points.map((p) => [p.lat, p.lon]);
  routeLine = L.polyline(latLngs, { color: accentColor(), weight: 4, opacity: 0.9, className: 'route-line' }).addTo(map);

  routeHitLine = L.polyline(latLngs, {
    color: '#000',
    weight: ROUTE_HIT_WEIGHT,
    opacity: 0.001,
    className: 'route-hit-line',
  }).addTo(map);
  routeHitLine.on('click', (event) => {
    L.DomEvent.stopPropagation(event);
    onRouteClick(event.latlng.lat, event.latlng.lng);
  });
  // While placing a photo, the profile's hover dot shows the point on the
  // line a click would pick (a mouse only: touch has no hover).
  routeHitLine.on('mousemove', (event) => {
    if (placementMode) {
      const snapped = snapToRoute(event.latlng);
      showHoverMarker(snapped.lat, snapped.lng);
    }
  });
  routeHitLine.on('mouseout', () => {
    if (placementMode) {
      hideHoverMarker();
    }
  });

  map.invalidateSize();
  map.fitBounds(routeLine.getBounds(), { paddingTopLeft: [32, 32], paddingBottomRight: [32, 32 + getBottomInset()] });
}

/**
 * The point on the drawn route nearest `latlng`, on screen. Only for where a
 * pin or the hover dot sits while the pointer moves: the route point an
 * anchor actually gets is still resolved in Python
 * (`resolveAnchorCandidates`).
 * @param {{lat: number, lng: number}} latlng
 * @returns {{lat: number, lng: number}}
 */
function snapToRoute(latlng) {
  const point = routeLine?.closestLayerPoint(map.latLngToLayerPoint(latlng));
  return point ? map.layerPointToLatLng(point) : latlng;
}

/**
 * Turns "a photo is waiting to be placed" on or off: a crosshair over the
 * route, the route line pulsing (css/map.css, `.is-placing`), and the hover
 * dot following the pointer along it.
 * @param {boolean} on
 */
export function setPlacementMode(on) {
  placementMode = on;
  map.getContainer().classList.toggle('is-placing', on);
  if (!on) {
    hideHoverMarker();
  }
}

/** Shows (or moves) the dot marking the profile chart's hovered position on the route. */
export function showHoverMarker(lat, lon) {
  if (!hoverMarker) {
    hoverMarker = L.circleMarker([lat, lon], { radius: 7, color: '#ffffff', weight: 2.5, fillOpacity: 1, interactive: false });
  }
  hoverMarker.setLatLng([lat, lon]);
  if (!map.hasLayer(hoverMarker)) {
    // Re-read on every show rather than once, in case the theme changed since.
    hoverMarker.setStyle({ fillColor: accentColor() });
    hoverMarker.addTo(map);
  }
}

/** Removes the profile hover dot, if shown. */
export function hideHoverMarker() {
  if (hoverMarker && map.hasLayer(hoverMarker)) {
    map.removeLayer(hoverMarker);
  }
}

const PIN_KINDS = new Set(['anchor', 'stop', 'photo']);

/** @typedef {import('./markerList.js').PinKind} PinKind */

/**
 * A numbered pin. Every kind is the same circle, colored by its
 * `.<kind>-pin` rule in css/map.css: a stop is a real pause, not just a timed
 * point, and a photo anchor came from a photo. An unknown kind is an anchor.
 * A `photo-unresolved` pin (a photo that still needs its pass picked) is a
 * photo pin with no number, under a "!" bubble.
 * @param {PinKind} kind
 * @param {number} number
 */
function pinIcon(kind, number) {
  const unresolved = kind === 'photo-unresolved';
  const pinKind = unresolved ? 'photo' : PIN_KINDS.has(kind) ? kind : 'anchor';
  const html = unresolved
    ? '<div class="photo-pin is-unresolved"><span class="pin-badge" aria-hidden="true">!</span></div>'
    : `<div class="${pinKind}-pin">${number}</div>`;
  return L.divIcon({
    className: `${pinKind}-pin-wrapper`,
    html,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}

/**
 * A layer of numbered pins with its own ids: each marker list (anchors,
 * stops) gets one, so their ids never meet. The shape is markerList.js's
 * `PinLayer`, which is all a list knows of the map.
 * @returns {import('./markerList.js').PinLayer}
 */
export function createPinLayer() {
  const markers = new Map();

  return {
    /**
     * Adds a numbered pin of `kind` (each kind is colored by its css/map.css rule).
     *
     * A `draggable` pin can only be dragged along the route: it snaps to the
     * line on every move. Once dropped somewhere new, `onDragEnd` gets where
     * (a task later, after the drop's click), and a `revert()` that puts the
     * pin back where its item is (where it was added or last `move`d); the
     * pin stays at the drop point until the caller either reverts it or moves
     * it properly (`move`). A revert from an earlier drop does nothing once
     * the pin has been dragged again. Without an `onDragEnd` every drop reverts.
     *
     * @param {number} id - the list's id for the item, to look the pin back up
     * @param {number} lat
     * @param {number} lon
     * @param {number} number - the number drawn inside the pin
     * @param {{
     *   onClick?: (id: number) => void,
     *   kind?: PinKind,
     *   draggable?: boolean,
     *   onDragEnd?: (id: number, lat: number, lon: number, revert: () => void) => void,
     *   title?: string,
     * }} [opts] - `title` is the pin's tooltip (a draggable pin has its own)
     */
    add(id, lat, lon, number, { onClick, kind = 'anchor', draggable = false, onDragEnd, title = '' } = {}) {
      const marker = L.marker([lat, lon], {
        icon: pinIcon(kind, number),
        draggable,
        title: draggable ? 'Drag along the route to move' : title,
      }).addTo(map);
      if (onClick) {
        marker.on('click', () => onClick(id));
      }
      // `home` is where the pin's item really is: where it was added, or last
      // moved to. A dropped pin waits away from it until the caller decides.
      const entry = { marker, kind, home: L.latLng(lat, lon) };
      if (draggable) {
        wireDrag(entry, (end, revert) => (onDragEnd ? onDragEnd(id, end.lat, end.lng, revert) : revert()));
      }
      markers.set(id, entry);
    },

    /** Puts an existing pin somewhere else (an item that was moved), keeping its number. */
    move(id, lat, lon) {
      const entry = markers.get(id);
      if (entry) {
        entry.home = L.latLng(lat, lon);
        entry.marker.setLatLng(entry.home);
      }
    },

    /** Removes the pin with the given id, if there is one. */
    remove(id) {
      const entry = markers.get(id);
      if (entry) {
        map.removeLayer(entry.marker);
        markers.delete(id);
      }
    },

    /** Re-labels the pins 1...N following `orderedIds`, without recreating them. */
    renumber(orderedIds) {
      orderedIds.forEach((id, index) => {
        const entry = markers.get(id);
        entry?.marker.setIcon(pinIcon(entry.kind, index + 1));
      });
    },

    /** Pans (without zooming) the map so the pin is centered in the map's visible part. */
    panTo(id) {
      const entry = markers.get(id);
      if (!entry) {
        return;
      }
      // Centring on a point half the covered height below the pin puts the pin
      // itself in the middle of what's left uncovered.
      const zoom = map.getZoom();
      const target = map.project(entry.marker.getLatLng(), zoom).add([0, getBottomInset() / 2]);
      map.panTo(map.unproject(target, zoom));
    },

    /** Toggles the `.is-active` style on a pin (sidebar-row hover/click sync). */
    highlight(id, isActive) {
      markers.get(id)?.marker.getElement()?.classList.toggle('is-active', isActive);
    },

    /**
     * Toggles the `.is-dimmed` style on a pin (an item that is kept but
     * currently unused). Call it after `renumber`, whose `setIcon` replaces the
     * pin's element and so drops the class.
     */
    setDimmed(id, dimmed) {
      markers.get(id)?.marker.getElement()?.classList.toggle('is-dimmed', dimmed);
    },
  };
}

/**
 * Makes a draggable pin snap to the route while dragged, and decides what a
 * drop means: `onDrop` gets a pin dropped away from its `home`, with the
 * drop point and a `revert`.
 * @param {{ marker: ReturnType<typeof L.marker>, home: ReturnType<typeof L.latLng> }} entry
 * @param {(end: {lat: number, lng: number}, revert: () => void) => void} onDrop
 */
function wireDrag(entry, onDrop) {
  const { marker } = entry;
  // Counts drags, so a revert left over from an earlier drop (its
  // confirmation closed only when the next drop opened another) can't put
  // the pin back under the newer one.
  let dragGeneration = 0;
  marker.on('dragstart', () => {
    dragGeneration += 1;
  });
  // By 'drag' Leaflet has already moved the marker to the pointer.
  marker.on('drag', () => marker.setLatLng(snapToRoute(marker.getLatLng())));
  marker.on('dragend', () => {
    const generation = dragGeneration;
    const end = marker.getLatLng();
    // Always back to `home`, never to where this drag started: that may
    // be an earlier drop that was never confirmed.
    const revert = () => {
      if (generation === dragGeneration) {
        marker.setLatLng(entry.home);
      }
    };
    if (entry.home.equals(end)) {
      return;
    }
    // Snapping can leave the pin away from the pointer, so the click that
    // follows this mouseup lands on the map (or, on a phone, on the
    // dialog's backdrop), and Leaflet doesn't suppress it there. The
    // browser dispatches that click before any timer, so waiting one keeps
    // it from closing the confirmation the drop opens, which reverted it.
    window.setTimeout(() => onDrop(end, revert));
  });
}

/**
 * Opens a Leaflet popup at the given location and lets the caller fill it.
 * popoverHost.js calls this on a desktop; on a phone the same content goes in
 * a dialog instead.
 *
 * @param {number} lat
 * @param {number} lon
 * @param {import('../placement/popoverHost.js').BuildContent} buildContent -
 *   its `updateLayout()` re-anchors the popup against the map, so a content
 *   size change (the stop popover's widening) doesn't leave it
 *   mis-anchored relative to its tip
 * @param {{ onClose?: () => void }} [opts] - `onClose` runs once when the
 *   popup goes away, however that happens: its own `close()`, the popup's ×,
 *   a click elsewhere, Esc, or another popup replacing it
 */
export function openPopup(lat, lon, buildContent, { onClose } = {}) {
  const container = document.createElement('div');
  // maxWidth just caps auto-sizing; the actual widths are forced via CSS
  // `!important` (240px normally, 492px for the stop popover's widened
  // "Start & end time" mode), so raising this doesn't change how any other
  // popover renders — it only stops Leaflet clamping the widened one back down.
  const popup = L.popup({ closeButton: true, className: 'anchor-popover', maxWidth: 500 }).setLatLng([lat, lon]);
  if (onClose) {
    popup.once('remove', onClose);
  }
  buildContent(container, () => map.closePopup(popup), () => popup.update());
  popup.setContent(container).openOn(map);
}
