// Shared dual-mode time input: a segmented Duration/Absolute toggle, one
// editable field for whichever mode is active, and a live-computed,
// non-editable preview of the other value underneath. Used both for the
// main start-time control ("Duration" / "End time") and for each anchor
// popover ("Duration since start" / "Time of day") — same shape, different
// mode pair, selected via `variant`.

function pad(n) {
  return String(n).padStart(2, '0');
}

function formatDuration(totalSeconds) {
  const clamped = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(clamped / 3600);
  const minutes = Math.floor((clamped % 3600) / 60);
  return `${hours}h ${pad(minutes)}m`;
}

function formatClock(date) {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatDateTime(date) {
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function createTimeToggle({ container, variant, getReferenceTime, onChange }) {
  const state = { mode: 'duration' };
  const otherKey = variant === 'durationOrEnd' ? 'end' : 'timeOfDay';

  const modesEl = document.createElement('div');
  modesEl.className = 'time-toggle-modes segmented';
  modesEl.setAttribute('role', 'tablist');

  const durationBtn = document.createElement('button');
  durationBtn.type = 'button';
  durationBtn.className = 'segmented-option is-active';
  durationBtn.textContent = variant === 'durationOrEnd' ? 'Duration' : 'Duration since start';
  durationBtn.setAttribute('role', 'tab');

  const otherBtn = document.createElement('button');
  otherBtn.type = 'button';
  otherBtn.className = 'segmented-option';
  otherBtn.textContent = variant === 'durationOrEnd' ? 'End time' : 'Time of day';
  otherBtn.setAttribute('role', 'tab');

  modesEl.append(durationBtn, otherBtn);

  const fieldsEl = document.createElement('div');
  fieldsEl.className = 'time-toggle-fields';

  const durationFields = document.createElement('div');
  durationFields.className = 'duration-fields';

  const hoursInput = document.createElement('input');
  hoursInput.type = 'number';
  hoursInput.min = '0';
  hoursInput.step = '1';
  hoursInput.value = '0';
  hoursInput.className = 'text-input duration-input';
  hoursInput.setAttribute('aria-label', 'Hours');

  const hoursSuffix = document.createElement('span');
  hoursSuffix.className = 'input-suffix';
  hoursSuffix.textContent = 'h';

  const minutesInput = document.createElement('input');
  minutesInput.type = 'number';
  minutesInput.min = '0';
  minutesInput.max = '59';
  minutesInput.step = '1';
  minutesInput.value = '0';
  minutesInput.className = 'text-input duration-input';
  minutesInput.setAttribute('aria-label', 'Minutes');

  const minutesSuffix = document.createElement('span');
  minutesSuffix.className = 'input-suffix';
  minutesSuffix.textContent = 'm';

  durationFields.append(hoursInput, hoursSuffix, minutesInput, minutesSuffix);

  const otherFields = document.createElement('div');
  otherFields.className = 'other-fields hidden';

  const otherInput = document.createElement('input');
  otherInput.className = 'text-input';
  otherInput.type = variant === 'durationOrEnd' ? 'datetime-local' : 'time';
  otherFields.append(otherInput);

  fieldsEl.append(durationFields, otherFields);

  const previewEl = document.createElement('p');
  previewEl.className = 'time-toggle-preview';

  container.append(modesEl, fieldsEl, previewEl);

  function currentReference() {
    const ref = getReferenceTime();
    return ref instanceof Date && !Number.isNaN(ref.getTime()) ? ref : null;
  }

  function resolve() {
    const reference = currentReference();
    if (!reference) {
      return { isValid: false };
    }

    if (state.mode === 'duration') {
      const hours = Number(hoursInput.value);
      const minutes = Number(minutesInput.value);
      if (!Number.isFinite(hours) || !Number.isFinite(minutes) || hours < 0 || minutes < 0) {
        return { isValid: false };
      }
      const durationSeconds = hours * 3600 + minutes * 60;
      if (durationSeconds <= 0) {
        return { isValid: false };
      }
      const resolvedDate = new Date(reference.getTime() + durationSeconds * 1000);
      return { isValid: true, mode: 'duration', resolvedDate, durationSeconds };
    }

    if (otherKey === 'end') {
      if (!otherInput.value) {
        return { isValid: false };
      }
      const resolvedDate = new Date(otherInput.value);
      if (Number.isNaN(resolvedDate.getTime())) {
        return { isValid: false };
      }
      const durationSeconds = (resolvedDate.getTime() - reference.getTime()) / 1000;
      if (durationSeconds <= 0) {
        return { isValid: false };
      }
      return { isValid: true, mode: 'end', resolvedDate, durationSeconds };
    }

    if (!otherInput.value) {
      return { isValid: false };
    }
    const [hh, mm] = otherInput.value.split(':').map(Number);
    if (!Number.isFinite(hh) || !Number.isFinite(mm)) {
      return { isValid: false };
    }
    const resolvedDate = new Date(reference);
    resolvedDate.setHours(hh, mm, 0, 0);
    const durationSeconds = (resolvedDate.getTime() - reference.getTime()) / 1000;
    return { isValid: durationSeconds >= 0, mode: 'timeOfDay', resolvedDate, durationSeconds };
  }

  function updatePreview() {
    const reference = currentReference();
    if (!reference) {
      previewEl.textContent =
        variant === 'durationOrEnd'
          ? 'Set a start time to see the computed end time.'
          : 'Set the route start time first.';
      onChange({ isValid: false });
      return;
    }

    const result = resolve();
    if (!result.isValid) {
      previewEl.textContent =
        state.mode === 'duration' ? 'Enter a duration greater than zero.' : 'Enter a valid time.';
      onChange({ isValid: false });
      return;
    }

    if (state.mode === 'duration') {
      previewEl.textContent =
        variant === 'durationOrEnd' ? `Ends at ${formatDateTime(result.resolvedDate)}` : `At ${formatClock(result.resolvedDate)}`;
    } else {
      previewEl.textContent = `Duration: ${formatDuration(result.durationSeconds)}`;
    }

    onChange(result);
  }

  function setMode(mode) {
    state.mode = mode;
    durationBtn.classList.toggle('is-active', mode === 'duration');
    otherBtn.classList.toggle('is-active', mode !== 'duration');
    durationBtn.setAttribute('aria-selected', String(mode === 'duration'));
    otherBtn.setAttribute('aria-selected', String(mode !== 'duration'));
    durationFields.classList.toggle('hidden', mode !== 'duration');
    otherFields.classList.toggle('hidden', mode === 'duration');
    updatePreview();
  }

  durationBtn.addEventListener('click', () => setMode('duration'));
  otherBtn.addEventListener('click', () => setMode(otherKey));
  [hoursInput, minutesInput, otherInput].forEach((el) => el.addEventListener('input', updatePreview));

  setMode('duration');

  return {
    refresh: updatePreview,
    getResult: resolve,
  };
}
