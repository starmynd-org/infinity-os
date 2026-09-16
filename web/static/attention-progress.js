/* Re-read counts after an act; receipts are history, never pending-row counts. */
(function () {
  'use strict';
  const section = document.querySelector('[data-attention-queue]');
  if (!section) return;
  let revision = 0;
  function link(host, label, offset, rel) {
    const url = new URL(location.href);
    url.hash = '';
    url.searchParams.set('offset', String(offset));
    url.searchParams.set('limit', section.dataset.attLimit || '7');
    const a = document.createElement('a');
    a.className = 'att-link';
    a.textContent = label;
    a.href = url.pathname + url.search;
    if (rel) a.rel = rel;
    host.appendChild(a);
  }
  function receiptRecovery(pager, pending, receipt) {
    section.querySelectorAll('[data-att-next]').forEach(node => node.remove());
    if (!receipt || !receipt.isConnected || !section.contains(receipt)) return;
    const host = document.createElement('div');
    host.dataset.attNext = '1';
    let next = receipt.nextElementSibling;
    while (next && !next.matches('[data-queue-row]')) next = next.nextElementSibling;
    if (pending && next) {
      const a = document.createElement('a');
      a.className = 'att-link';
      a.href = '#' + encodeURIComponent(next.id);
      a.textContent = 'Next pending row on this page';
      host.appendChild(a);
    } else {
      const recovery = pager.querySelector('a[rel="next"]') || pager.querySelector('a');
      if (recovery) host.appendChild(recovery.cloneNode(true));
    }
    if (host.childElementCount) receipt.querySelector('td').appendChild(host);
  }
  section.addEventListener('attention:changed', async function (event) {
    const ownRevision = ++revision;
    // land() has just focused this act's receipt. Keep its identity if the user
    // moves focus or acts out of row order while the fresh read is pending.
    const focused = document.activeElement;
    const receipt = focused && focused.closest('[data-att-receipt]');
    const count = section.querySelector('[data-att-counts]');
    const pager = section.querySelector('[data-att-pages]');
    count.textContent = 'Reading the remaining queue…';
    pager.replaceChildren();
    section.querySelectorAll('[data-att-next]').forEach(node => node.remove());
    const outcome = event.detail || {};
    const removes = outcome.verb === 'accept work' || outcome.verb === 'accept' ||
      (outcome.subject_type === 'recommendation' && ['recommend accept', 'recommend reject'].includes(outcome.verb));
    if (!removes) {
      count.textContent = 'The action is recorded. Reload to read the changed queue and continue.';
      section.querySelector('[data-attention-denominators]').textContent = 'This action can restore or reorder work. The earlier counts and page positions are no longer current.';
      link(pager, 'Reload queue from the start', 0);
      receiptRecovery(pager, false, receipt);
      return;
    }
    try {
      const response = await fetch(location.href.split('#')[0], {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('read refused');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const fresh = doc.querySelector('[data-attention-queue]');
      if (!fresh || fresh.dataset.attTotal === '') throw new Error('counts unavailable');
      const total = Number(fresh.dataset.attTotal);
      const offset = Number(fresh.dataset.attOffset);
      const limit = Number(fresh.dataset.attLimit);
      if (![total, offset, limit].every(Number.isFinite)) throw new Error('counts invalid');
      if (ownRevision !== revision) return;
      const shown = section.querySelectorAll('[data-queue-row]').length;
      if (shown > total - offset) throw new Error('visible rows changed elsewhere');
      const visible = Array.from(section.querySelectorAll('[data-queue-row]'), r => r.dataset.queueRow);
      const current = Array.from(fresh.querySelectorAll('[data-queue-row]'), r => r.dataset.queueRow);
      if (visible.some((id, index) => current[index] !== id)) throw new Error('queue order changed elsewhere');
      const receipts = section.querySelectorAll('[data-att-receipt]').length;
      section.dataset.attOffset = String(offset);
      section.dataset.attLimit = String(limit);
      count.textContent = shown + ' of ' + total + ' pending shown · ' + receipts + ' receipt' + (receipts === 1 ? '' : 's');
      section.querySelector('[data-attention-denominators]').textContent =
        fresh.dataset.attAdmitted + ' admitted; ' + fresh.dataset.attRefused + ' refused; ' +
        fresh.dataset.attBlocked + ' blocked; ' + fresh.dataset.attDeferred + ' deferred. ' +
        Math.max(0, total - offset - shown) + ' pending after these rows. Counts read back from the current queue.';
      if (offset) link(pager, 'Previous', Math.max(0, offset - limit), 'prev');
      if (offset + shown < total) link(pager, 'Next', offset + shown, 'next');
      receiptRecovery(pager, true, receipt);
    } catch (_) {
      if (ownRevision !== revision) return;
      count.textContent = 'The action is recorded; remaining counts could not be refreshed.';
      section.querySelector('[data-attention-denominators]').textContent = 'Reload the queue before relying on its counts or page positions.';
      pager.replaceChildren();
      link(pager, 'Reload queue from the start', 0);
      receiptRecovery(pager, false, receipt);
    }
  });
})();
