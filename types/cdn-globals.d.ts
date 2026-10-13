// The libraries index.html loads from a CDN as plain scripts, so they are
// globals rather than imports. Typed loosely on purpose: tsc checks our code,
// not theirs, and @types/leaflet would be one more version to keep in step
// with the pinned CDN file.
declare const L: any;
declare namespace L {
  type Map = any;
}
declare const exifr: any;
declare function loadPyodide(options?: { indexURL?: string }): Promise<any>;
