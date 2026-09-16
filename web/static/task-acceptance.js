/* Task acceptance uses the ordinary guarded door and rereads this task's own page. */
(function () {
  'use strict';
  const root = document.querySelector('[data-task-page]');
  if (!root) return;
  const feedback = root.querySelector('[data-task-feedback]');
  let busy = false;
  let errorIndex = 0;
  function owned(form) {
    const action = form && form.querySelector('input[name="action"]');
    return form && root.contains(form) && action && ['accept_work', 'unaccept'].includes(action.value);
  }
  function gate(field) {
    // One normalization rule: the shared, rebound form gate owns this field.
    field.dispatchEvent(new Event('input'));
  }
  function announce(text) {
    feedback.hidden = false;
    feedback.textContent = text;
    feedback.focus();
  }
  function refusal(form, button, message) {
    let error = form.querySelector('[data-task-error]');
    if (!error) {
      error = document.createElement('p');
      error.dataset.taskError = '1';
      error.id = 'task-act-error-' + (++errorIndex);
      error.className = 'task-error';
      error.setAttribute('role', 'alert');
      form.appendChild(error);
    }
    error.textContent = message;
    button.disabled = false;
    const field = form.querySelector('[data-gate]');
    const focusTarget = field || button;
    const descriptions = (focusTarget.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
    if (!descriptions.includes(error.id)) descriptions.push(error.id);
    focusTarget.setAttribute('aria-describedby', descriptions.join(' '));
    if (field) gate(field);
    focusTarget.focus();
  }
  async function submit(form) {
    if (busy) return;
    busy = true;
    const button = form.querySelector('button[type="submit"], button.act, button.sec');
    const error = form.querySelector('[data-task-error]');
    if (error) {
      form.querySelectorAll('[aria-describedby]').forEach(function (target) {
        target.setAttribute('aria-describedby', target.getAttribute('aria-describedby').split(' ').filter(id => id !== error.id).join(' '));
      });
      error.remove();
    }
    button.disabled = true;
    try {
      const response = await fetch(form.getAttribute('action'), {method: 'POST', body: new FormData(form), credentials: 'same-origin'});
      let result;
      try { result = await response.json(); } catch (_) { throw new Error('unreadable response'); }
      if (!response.ok || !result.ok) {
        refusal(form, button, result.error || 'The decision was refused. Reload the current task state.');
        return;
      }
      announce(result.receipt || 'The decision was recorded.');
      try {
        const freshResponse = await fetch(location.href.split('#')[0], {credentials: 'same-origin', cache: 'no-store'});
        if (!freshResponse.ok) throw new Error('task read refused');
        const doc = new DOMParser().parseFromString(await freshResponse.text(), 'text/html');
        const state = doc.querySelector('[data-task-state]');
        const receipts = doc.querySelector('[data-task-receipts]');
        if (!state || !receipts) throw new Error('task state unavailable');
        root.querySelector('[data-task-state]').replaceWith(state);
        root.querySelector('[data-task-receipts]').replaceWith(receipts);
        document.dispatchEvent(new Event('console:rewire'));
        root.querySelectorAll('[data-gate]').forEach(gate);
        feedback.focus();
      } catch (_) {
        root.querySelector('[data-task-state]').hidden = true;
        root.querySelector('[data-task-receipts]').hidden = true;
        announce((result.receipt || 'The decision was recorded.') + ' Current task state could not be read back. Reload before another decision.');
        const reload = document.createElement('a');
        reload.href = location.pathname + location.search;
        reload.textContent = 'Reload task';
        feedback.append(' ', reload);
      }
    } catch (_) {
      refusal(form, button, 'The response could not be confirmed. Reload the task to check whether the decision was recorded before trying again.');
    } finally {
      busy = false;
    }
  }
  // Capture before the generic room handler; one user submission makes one POST.
  document.addEventListener('submit', function (event) {
    const form = event.target.closest('form');
    if (!owned(form)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    submit(form);
  }, true);
  root.addEventListener('click', function (event) {
    const toggle = event.target.closest('[data-toggle]');
    if (!toggle || !toggle.dataset.toggle.startsWith('un-')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const panel = document.getElementById(toggle.dataset.toggle);
    if (panel) panel.hidden = !panel.hidden;
  }, true);
})();
