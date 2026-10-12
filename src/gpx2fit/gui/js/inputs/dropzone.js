// The GPX and photo dropzones: a focusable box that takes dropped files, and
// opens its hidden file input on a click, Enter or Space.

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
 */
export function wireDropzone(dropzoneEl, inputEl, onFiles) {
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
