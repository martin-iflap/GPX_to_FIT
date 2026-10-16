// The GPX and photo dropzones: a focusable box that takes dropped files, and
// opens its hidden file input on a click, Enter or Space. Also the GPX
// dropzone's file line, which follows an upload while it loads.

/**
 * Hands every chosen file to `onFiles`, then clears the input, so that
 * choosing the same file again (e.g. to start over) still fires 'change'.
 * The files are copied out first: clearing empties `input.files`.
 * @param {HTMLInputElement} input
 * @param {(files: File[]) => void} onFiles
 */
export function wireFileInput(input, onFiles) {
  input.addEventListener('change', () => {
    const files = [...(input.files ?? [])];
    input.value = '';
    onFiles(files);
  });
}

/**
 * Wires a dropzone and its file input. `.is-dragover` marks the box while
 * files are dragged over it.
 * @param {HTMLElement} dropzoneEl
 * @param {HTMLInputElement} inputEl
 * @param {(files: File[]) => void} onFiles - called with the dropped or chosen
 *   files, possibly none
 * @param {{onIntent?: () => void}} [options] - `onIntent` is called whenever
 *   the user heads for the box (a hover, a focus, a drag over it). A moment
 *   before any file arrives, so work the files will need can start early.
 *   It runs often, so it must be cheap to call again.
 */
export function wireDropzone(dropzoneEl, inputEl, onFiles, { onIntent } = {}) {
  if (onIntent) {
    // pointerenter fires for a touch too, just before the tap.
    for (const eventName of ['pointerenter', 'focus', 'dragenter']) {
      dropzoneEl.addEventListener(eventName, () => onIntent());
    }
  }
  dropzoneEl.addEventListener('click', () => inputEl.click());
  dropzoneEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      inputEl.click();
    }
  });
  wireFileInput(inputEl, onFiles);

  // preventDefault on both is what makes the box a drop target at all, and
  // keeps the browser from opening a dropped file in place of the app.
  ['dragenter', 'dragover'].forEach((eventName) => {
    dropzoneEl.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzoneEl.classList.add('is-dragover');
    });
  });
  ['dragleave', 'drop'].forEach((eventName) => {
    dropzoneEl.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzoneEl.classList.remove('is-dragover');
    });
  });
  dropzoneEl.addEventListener('drop', (event) => {
    onFiles([...(event.dataTransfer?.files ?? [])]);
  });
}

/**
 * What an upload in progress is doing, as the dropzone says it: waiting its
 * turn behind another Python call, waiting for the runtime to load, or
 * parsing (see `pyodideBridge.parseGpx`'s stages).
 * @typedef {'queued'|import('../bridge/pyodideBridge.js').ParseStage} UploadStage
 */

/** @type {Record<UploadStage, string>} */
export const UPLOAD_STAGE_TEXT = {
  queued: 'Waiting…',
  engine: 'Loading the converter…',
  parse: 'Reading the route…',
};

/**
 * @typedef {object} Upload
 * @property {(stage: UploadStage) => void} setStage
 * @property {() => void} succeed - the route is loaded: its name stays
 * @property {() => void} fail - back to the route still loaded, which is
 *   this one's if it has already succeeded
 */

/**
 * The GPX dropzone's file line. It names the chosen file and what its upload
 * is doing at once, since the parse can wait seconds for the runtime and the
 * status line is out of sight at the bottom of the sidebar. Once no upload is
 * in progress it names the route loaded, if any.
 *
 * Uploads are parsed one at a time in the order chosen, so the newest is the
 * one shown, and the last to finish. An earlier one that loads meanwhile
 * still becomes the route a failed newer one goes back to, since Python
 * then holds it.
 *
 * @param {{dropzoneEl: HTMLElement, nameEl: HTMLElement, progressEl: HTMLElement}} elements -
 *   `.is-busy` and `aria-busy` go on `dropzoneEl` while an upload is in progress
 * @returns {{begin: (fileName: string) => Upload}}
 */
export function createUploadLabel({ dropzoneEl, nameEl, progressEl }) {
  let loadedName = '';
  /** @type {{fileName: string, stage: UploadStage}[]} */
  const pending = [];

  function render() {
    const shown = pending.at(-1);
    const name = shown ? shown.fileName : loadedName;
    nameEl.textContent = name;
    nameEl.hidden = !name;
    progressEl.textContent = shown ? UPLOAD_STAGE_TEXT[shown.stage] : '';
    progressEl.hidden = !shown;
    dropzoneEl.classList.toggle('is-busy', Boolean(shown));
    if (shown) {
      dropzoneEl.setAttribute('aria-busy', 'true');
    } else {
      dropzoneEl.removeAttribute('aria-busy');
    }
  }

  /** @param {string} fileName */
  function begin(fileName) {
    /** @type {{fileName: string, stage: UploadStage}} */
    const upload = { fileName, stage: 'queued' };
    pending.push(upload);
    render();

    // Finishing twice must not take another upload off the list.
    const finish = () => {
      const index = pending.indexOf(upload);
      if (index !== -1) {
        pending.splice(index, 1);
      }
    };
    return {
      setStage(stage) {
        upload.stage = stage;
        render();
      },
      succeed() {
        finish();
        loadedName = fileName;
        render();
      },
      fail() {
        finish();
        render();
      },
    };
  }

  return { begin };
}
