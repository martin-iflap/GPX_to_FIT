// Leaflet map: theme-aware CARTO basemap, route polyline rendering, and
// numbered anchor pin markers. Route-click detection is attached to the
// polyline layer itself (not the map), so only clicks on the route line
// place an anchor.

const TILE_LAYERS = {
  light: {
    url: 'https://{s}.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
    subdomains: ['server', 'services'],
  },
  dark: {
    url: 'https://{s}.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
    subdomains: ['server', 'services'],
  },
};

let map = null;
let tileLayer = null;
let routeLine = null;
const markers = new Map();

export function initMap(containerId) {
  map = L.map(containerId, { zoomControl: true, attributionControl: true });
  map.setView([46.8, 8.2], 8);
  window.addEventListener('resize', () => map.invalidateSize());
  return map;
}

export function setMapTheme(theme) {
  const config = theme === 'dark' ? TILE_LAYERS.dark : TILE_LAYERS.light;
  if (tileLayer) {
    map.removeLayer(tileLayer);
  }
  tileLayer = L.tileLayer(config.url, {
    attribution: config.attribution,
    subdomains: config.subdomains,
    maxZoom: 18,
    maxNativeZoom: 16,
  });
  tileLayer.addTo(map);
}

export function updateRouteColor(color) {
  if (routeLine) {
    routeLine.setStyle({ color });
  }
}

export function renderRoute(points, onRouteClick) {
  if (routeLine) {
    map.removeLayer(routeLine);
    routeLine = null;
  }

  const latLngs = points.map((p) => [p.lat, p.lon]);
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#007aff';

  routeLine = L.polyline(latLngs, { color: accent, weight: 4, opacity: 0.9 }).addTo(map);
  routeLine.on('click', (event) => {
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

export function addMarker(id, lat, lon, number, { onClick } = {}) {
  const marker = L.marker([lat, lon], { icon: pinIcon(number) }).addTo(map);
  if (onClick) {
    marker.on('click', () => onClick(id));
  }
  markers.set(id, marker);
  return marker;
}

export function removeMarker(id) {
  const marker = markers.get(id);
  if (marker) {
    map.removeLayer(marker);
    markers.delete(id);
  }
}

export function renumberMarkers(orderedIds) {
  orderedIds.forEach((id, index) => {
    const marker = markers.get(id);
    if (marker) {
      marker.setIcon(pinIcon(index + 1));
    }
  });
}

export function panToMarker(id) {
  const marker = markers.get(id);
  if (marker) {
    map.panTo(marker.getLatLng());
  }
}

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

export function openAnchorPopup(lat, lon, buildContent) {
  const container = document.createElement('div');
  buildContent(container, () => map.closePopup());
  L.popup({ closeButton: true, className: 'anchor-popover' })
    .setLatLng([lat, lon])
    .setContent(container)
    .openOn(map);
}

export function getMap() {
  return map;
}
