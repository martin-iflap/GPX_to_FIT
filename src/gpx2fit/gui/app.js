const statusEl = document.getElementById('status');
const runButton = document.getElementById('runButton');
const downloadLink = document.getElementById('downloadLink');
const startTimeInput = document.getElementById('startTime');
const durationInput = document.getElementById('duration');
const gpxFileInput = document.getElementById('gpxFile');

let pyodide = null;

async function ensurePyodide() {
  if (pyodide) {
    return pyodide;
  }

  pyodide = await loadPyodide({ indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.27.3/full/' });
  await pyodide.loadPackage(['micropip']);

  await pyodide.runPythonAsync(`
import micropip
await micropip.install('gpxpy')
await micropip.install('fit-tool')
`);

  const fetchText = async (path) => {
    const response = await fetch(path);
    if (!response.ok) {
      throw new Error(`Failed to load ${path}: ${response.status} ${response.statusText}`);
    }
    return response.text();
  };

  const backendFiles = [
    ['src/gpx2fit/__init__.py', ''],
    ['src/gpx2fit/core/__init__.py', ''],
    ['src/gpx2fit/core/pacing/__init__.py', ''],
    ['src/gpx2fit/core/models.py', await fetchText('/src/gpx2fit/core/models.py')],
    ['src/gpx2fit/core/gpx_reader.py', await fetchText('/src/gpx2fit/core/gpx_reader.py')],
    ['src/gpx2fit/core/fit_writer.py', await fetchText('/src/gpx2fit/core/fit_writer.py')],
    ['src/gpx2fit/core/pacing/combine.py', await fetchText('/src/gpx2fit/core/pacing/combine.py')],
  ];

  pyodide.globals.set('backend_files_json', JSON.stringify(backendFiles));

  await pyodide.runPythonAsync(`
import json
import pathlib
import sys

root = pathlib.Path('/workspace')
root.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, '/workspace/src')

for rel_path, content in json.loads(backend_files_json):
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
`);

  return pyodide;
}

function setStatus(message) {
  statusEl.textContent = message;
}

function resetDownload() {
  downloadLink.hidden = true;
  downloadLink.removeAttribute('href');
}

runButton.addEventListener('click', async () => {
  resetDownload();

  const file = gpxFileInput.files[0];
  if (!file) {
    setStatus('Please choose a GPX file first.');
    return;
  }

  const start = startTimeInput.value ? new Date(startTimeInput.value) : null;
  const duration = Number(durationInput.value);

  if (!start || Number.isNaN(start.getTime()) || !(duration > 0)) {
    setStatus('Please choose a valid start time and a positive duration.');
    return;
  }

  try {
    setStatus('Preparing backend...');
    const runtime = await ensurePyodide();

    const bytes = new Uint8Array(await file.arrayBuffer());
    runtime.globals.set('gpx_bytes', bytes);
    runtime.globals.set('start_iso', start.toISOString());
    runtime.globals.set('duration_seconds', duration);

    setStatus('Parsing GPX and creating the FIT output...');

    await runtime.runPythonAsync(`
import datetime as dt

from gpx2fit.core.gpx_reader import parse_gpx_bytes
from gpx2fit.core.fit_writer import write_fit
from gpx2fit.core.pacing.combine import simple_uniform_speed

track = parse_gpx_bytes(bytes(gpx_bytes.to_py()))
if not track.points:
    raise ValueError('No points were found in the GPX file.')

start = dt.datetime.fromisoformat(start_iso)
end = start + dt.timedelta(seconds=int(duration_seconds))

track.points[0].timestamp = start
track.points[-1].timestamp = end
simple_uniform_speed(track)
fit_bytes = write_fit(track)
`);

    const fitBytes = runtime.globals.get('fit_bytes').toJs({ create_proxies: false });
    const blob = new Blob([fitBytes], { type: 'application/octet-stream' });
    const url = URL.createObjectURL(blob);

    downloadLink.href = url;
    downloadLink.hidden = false;
    downloadLink.textContent = `Download FIT (${blob.size} bytes)`;
    setStatus('FIT file generated successfully.');
  } catch (error) {
    console.error(error);
    setStatus(`Error: ${error instanceof Error ? error.message : String(error)}`);
  }
});

startTimeInput.value = new Date(Date.now() + 60_000).toISOString().slice(0, 16);
