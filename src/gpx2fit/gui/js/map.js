// Leaflet map: theme-aware basemap, route polyline rendering, and numbered
// anchor pin markers. Route-click detection is attached to the polyline
// layer itself (not the map), so only clicks on the route line place an
// anchor.
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

import { THUNDERFOREST_API_KEY } from './config.js';

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

let map = null;
let tileLayer = null;
let routeLine = null;
let routeHitLine = null;
const markers = new Map();

// Screen-pixel width of the invisible click target drawn on top of the
// visible route line — the visible stroke (weight 4) is too thin to click
// reliably, so a much fatter, near-transparent line underneath it (opacity
// kept just above 0 rather than exactly 0, since a hard 0 risks the
// renderer treating the stroke as non-hit-testable) absorbs the click.
const ROUTE_HIT_WEIGHT = 22;

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
  window.addEventListener('resize', () => map.invalidateSize());
  return map;
}

/** Swaps the basemap tile layer. @param {'light'|'dark'} theme */
export function setMapTheme(theme) {
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
}

/** Recolors the already-rendered route line (e.g. to match the theme's accent color) without redrawing it. */
export function updateRouteColor(color) {
  if (routeLine) {
    routeLine.setStyle({ color });
  }
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
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#007aff';

  routeLine = L.polyline(latLngs, { color: accent, weight: 4, opacity: 0.9 }).addTo(map);

  routeHitLine = L.polyline(latLngs, {
    color: '#000',
    weight: ROUTE_HIT_WEIGHT,
    opacity: 0.001,
  }).addTo(map);
  routeHitLine.on('click', (event) => {
    L.DomEvent.stopPropagation(event);
    onRouteClick(event.latlng.lat, event.latlng.lng);
  });

  map.fitBounds(routeLine.getBounds(), { padding: [32, 32] });
  map.invalidateSize();
}

function pinIcon(number) {
  return L.divIcon({
    className: 'anchor-pin-wrapper',
    html: `<div class="anchor-pin">${number}</div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}

/**
 * Adds a numbered anchor pin to the map.
 *
 * @param {number} id - caller-assigned id (typically the anchor's id), used to look the marker back up
 * @param {number} lat
 * @param {number} lon
 * @param {number} number - the number drawn inside the pin
 * @param {{onClick?: (id: number) => void}} [opts]
 * @returns {L.Marker}
 */
export function addMarker(id, lat, lon, number, { onClick } = {}) {
  const marker = L.marker([lat, lon], { icon: pinIcon(number) }).addTo(map);
  if (onClick) {
    marker.on('click', () => onClick(id));
  }
  markers.set(id, marker);
  return marker;
}

/** Removes the pin with the given id, if one exists. */
export function removeMarker(id) {
  const marker = markers.get(id);
  if (marker) {
    map.removeLayer(marker);
    markers.delete(id);
  }
}

/** Re-labels existing pins 1...N following `orderedIds` — call after anchors are re-sorted, without recreating markers. */
export function renumberMarkers(orderedIds) {
  orderedIds.forEach((id, index) => {
    const marker = markers.get(id);
    if (marker) {
      marker.setIcon(pinIcon(index + 1));
    }
  });
}

/** Pans (without zooming) the map so the given pin is centered. */
export function panToMarker(id) {
  const marker = markers.get(id);
  if (marker) {
    map.panTo(marker.getLatLng());
  }
}

/** Toggles the `.is-active` style on a pin (used for sidebar-row hover/click sync). */
export function highlightMarker(id, isActive) {
  const marker = markers.get(id);
  if (!marker) {
    return;
  }
  const el = marker.getElement();
  if (el) {
    el.classList.toggle('is-active', isActive);
  }
}

/**
 * Opens a Leaflet popup at the given location and lets the caller fill it
 * with arbitrary DOM content (used for the anchor-time and
 * candidate-picker popovers).
 *
 * @param {number} lat
 * @param {number} lon
 * @param {(container: HTMLElement, close: () => void) => void} buildContent -
 *   receives an empty container to append content to, and a `close()`
 *   callback the content can invoke (e.g. from a confirm button) to dismiss the popup
 */
export function openAnchorPopup(lat, lon, buildContent) {
  const container = document.createElement('div');
  buildContent(container, () => map.closePopup());
  L.popup({ closeButton: true, className: 'anchor-popover' })
    .setLatLng([lat, lon])
    .setContent(container)
    .openOn(map);
}
