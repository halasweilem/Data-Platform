const $ = selector => document.querySelector(selector);
const state = { me: null, domain: null, files: [], selected: null, activeTopic: null, topicBrowsePath: '', retailCollection: null, retailCollectionRoles: {}, retailCollectionLabels: {}, topicLabels: {}, newWorkflowDraft: null, workflowMode: 'edit', retailMode: 'edit', retailRenaming: false, retailAddingContent: false, retailAddingSection: false, retailAddingSubsection: false, retailGroupPath: null, retailRenamingCategory: null, retailWizard: null, retailWizardDismissed: false, retailCreateWizard: null, workflowWizard: null, workflowWizardDismissed: false, raw: false, expanded: new Set(), requestId: 0, retailSection: 0, retailCategory: null, retailSearch: '', dirty: false, historyVersions: [], historyPending: [], historyView: 'approved', historySearch: '', historyAction: 'all', historyGroupBy: 'recent' };
const LAST_TAB_KEY = 'capital-data-studio.active-tab';
const LAST_SELECTION_KEY = 'capital-data-studio.selected-paths';
const LAST_TOPIC_KEY = 'capital-data-studio.selected-topics';
const LAST_RETAIL_COLLECTION_KEY = 'capital-data-studio.retail-collection';
const REFERENCE_REVIEW_KEY = 'capital-data-studio.reference-review';
const ACTIVE_SESSION_KEY = 'capital-data-studio.active-session';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

async function api(url, options = {}) {
  const headers = options.body instanceof FormData ? {} : {'Content-Type': 'application/json'};
  const response = await fetch(url, {headers, ...options});
  const data = await response.json().catch(() => ({error: 'Invalid server response'}));
  if (!response.ok && response.status !== 202) throw new Error(data.error || 'Request failed');
  return data;
}
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); setTimeout(() => $('#toast').classList.remove('show'), 2800); }
function showNotice(message, type, html = false) { $('#notice').className = `notice ${type}`; $('#notice')[html ? 'innerHTML' : 'textContent'] = message; $('#notice').hidden = false; }
function rememberTab(tab) { try { localStorage.setItem(LAST_TAB_KEY, tab); } catch {} }
function rememberedTab() { try { return localStorage.getItem(LAST_TAB_KEY); } catch { return null; } }
function rememberSelection(domain, path) { try { const saved = JSON.parse(localStorage.getItem(LAST_SELECTION_KEY) || '{}'); saved[domain] = path; localStorage.setItem(LAST_SELECTION_KEY, JSON.stringify(saved)); } catch {} }
function rememberedSelection(domain) { try { return JSON.parse(localStorage.getItem(LAST_SELECTION_KEY) || '{}')[domain] || null; } catch { return null; } }
function forgetSelection(domain) { try { const saved = JSON.parse(localStorage.getItem(LAST_SELECTION_KEY) || '{}'); delete saved[domain]; localStorage.setItem(LAST_SELECTION_KEY, JSON.stringify(saved)); } catch {} }
function rememberTopic(domain, topic) { try { const saved = JSON.parse(localStorage.getItem(LAST_TOPIC_KEY) || '{}'); saved[domain] = topic; localStorage.setItem(LAST_TOPIC_KEY, JSON.stringify(saved)); } catch {} }
function rememberedTopic(domain) { try { return JSON.parse(localStorage.getItem(LAST_TOPIC_KEY) || '{}')[domain] || null; } catch { return null; } }
function forgetTopic(domain) { try { const saved = JSON.parse(localStorage.getItem(LAST_TOPIC_KEY) || '{}'); delete saved[domain]; localStorage.setItem(LAST_TOPIC_KEY, JSON.stringify(saved)); } catch {} }
function rememberRetailCollection(path) { try { path ? localStorage.setItem(LAST_RETAIL_COLLECTION_KEY, path) : localStorage.removeItem(LAST_RETAIL_COLLECTION_KEY); } catch {} }
function rememberedRetailCollection() { try { return localStorage.getItem(LAST_RETAIL_COLLECTION_KEY) || null; } catch { return null; } }
function referenceReviewKey() { return `${state.domain}:${state.selected?.path || ''}`; }
function referenceReviewed() { try { return new Set(JSON.parse(localStorage.getItem(REFERENCE_REVIEW_KEY) || '{}')[referenceReviewKey()] || []); } catch { return new Set(); } }
function saveReferenceReviewed(fields) { try { const saved = JSON.parse(localStorage.getItem(REFERENCE_REVIEW_KEY) || '{}'); saved[referenceReviewKey()] = [...fields]; localStorage.setItem(REFERENCE_REVIEW_KEY, JSON.stringify(saved)); } catch {} }
function referenceReviewStatus(count) { const required = Array.from({length: count}, (_, index) => `item.${index}`); const reviewed = referenceReviewed(); return {required, reviewed, complete: required.every(field => reviewed.has(field))}; }
function referenceItemCount(data) { if (Array.isArray(data) && data.every(item => item && typeof item === 'object' && ('question' in item || 'answer' in item))) return data.length; if (data && typeof data === 'object' && Array.isArray(data.tips)) return data.tips.some(item => item && typeof item === 'object') ? data.tips.length : tipGroups(data.tips).length; if (data && typeof data === 'object' && Array.isArray(data.Rules)) return data.Rules.length; if (data && typeof data === 'object' && Array.isArray(data.overview)) return data.overview.length; return null; }
function updateReferenceMeta() { const count = referenceItemCount(state.selected?.data); if (count === null) return; const data = state.selected.data; const label = Array.isArray(data) ? 'FAQs' : Array.isArray(data?.tips) ? 'main tips' : Array.isArray(data?.Rules) ? 'rules' : 'overview sections'; $('#docMeta').textContent = `${count} ${label} · ready to review`; }
function referenceDocumentLabel(data) { if (Array.isArray(data) && referenceItemCount(data) !== null) return 'FAQ'; if (data && Array.isArray(data.tips)) return 'Tips'; if (data && Array.isArray(data.Rules)) return 'Rules'; if (data && (Array.isArray(data.overview) || Array.isArray(data.features) || Array.isArray(data.services))) return 'Overview'; return 'workflow'; }
function referenceReviewSummary(title, count) { const status = referenceReviewStatus(count); const checked = status.required.filter(field => status.reviewed.has(field)).length; return `<div class="reference-validation"><div><strong>${checked} of ${count} ${title.toLowerCase()} checked</strong><span>Confirm each item before selecting Approve.</span></div><b>${status.complete ? 'Ready for approval' : 'Validation required'}</b></div>`; }
function referenceReviewControl(index, label) { return `<label class="review-control reference-review-control"><input type="checkbox" data-reference-review="${index}" ${referenceReviewed().has(`item.${index}`) ? 'checked' : ''}><span>${esc(label)}</span></label>`; }
function wireReferenceReviews() { document.querySelectorAll('[data-reference-review]').forEach(input => input.onchange = () => { const reviewed = referenceReviewed(); const field = `item.${input.dataset.referenceReview}`; input.checked ? reviewed.add(field) : reviewed.delete(field); saveReferenceReviewed(reviewed); updateReferenceMeta(); renderEditor(); }); }

async function init() {
  try {
    const meta = await api('/api/auth/me');
    state.me = meta.user;
    $('#user').textContent = state.me.username + (state.me.approver ? ' · Approver' : '');
    state.domainLabels = Object.fromEntries(Object.entries(meta.domains).map(([key, value]) => [key, value.label]));
    const NAV_LABELS = {retail: 'Products Guidelines', steps: 'How to Guide'};
    $('#domains').innerHTML = Object.entries(meta.domains).map(([key, value]) => `<button data-domain="${key}">${esc(NAV_LABELS[key] || value.label)}</button>`).join('') + (meta.domains.steps || meta.domains.retail ? '<button id="historyNav">History</button>' : '') + (state.me.milvus_admin ? '<button id="connectedSystemsNav">Connected systems</button>' : '');
    document.querySelectorAll('[data-domain]').forEach(button => button.onclick = () => selectDomain(button.dataset.domain));
    if ($('#historyNav')) $('#historyNav').onclick = showHistory;
    if ($('#connectedSystemsNav')) $('#connectedSystemsNav').onclick = showConnectedSystems;
    const continuingSession = sessionStorage.getItem(ACTIVE_SESSION_KEY) === 'true';
    const savedTab = rememberedTab();
    const initialDomain = continuingSession && meta.domains[savedTab] ? savedTab : Object.keys(meta.domains)[0];
    if (continuingSession && savedTab === 'history') await showHistory();
    else if (continuingSession && ['milvus', 'connected_systems'].includes(savedTab) && state.me.milvus_admin) showConnectedSystems();
    else if (initialDomain) {
      await selectDomain(initialDomain);
      if (continuingSession) {
        const selectedPath = rememberedSelection(initialDomain);
        if (selectedPath && state.files.some(file => file.path === selectedPath)) await openFile(selectedPath);
        else if (initialDomain === 'steps') {
          const topic = rememberedTopic('steps');
          if (topic && state.files.some(file => file.path.startsWith(`${topic}/`))) { state.activeTopic = topic; renderFiles(); renderTopicExplorer(); }
        } else if (initialDomain === 'retail') {
          const collection = rememberedRetailCollection();
          if (collection && state.files.some(file => file.path.startsWith(`${collection}/`))) { state.retailCollection = collection; renderFiles(); renderRetailCollection(collection); }
        }
      }
    }
    sessionStorage.setItem(ACTIVE_SESSION_KEY, 'true');
  } catch { sessionStorage.removeItem(ACTIVE_SESSION_KEY); location = '/login.html'; }
}

async function selectDomain(domain) {
  const requestId = ++state.requestId;
  state.domain = domain; rememberTab(domain); state.selected = null; state.activeTopic = null; state.topicBrowsePath = ''; $('#editor').hidden = true; $('#empty').hidden = false;
  $('#empty').className = 'empty'; $('#empty').innerHTML = '<div class="empty-icon">⌁</div><h2>Select a topic or workflow to get started</h2><p>Pick an item on the left to review or edit it, then submit it for approval before it goes live in the mobile assistant.</p>';
  $('#historyPage').hidden = true; $('#milvusPage').hidden = true; $('main').classList.remove('history-mode'); document.querySelector('main>aside').hidden = false;
  $('#newWorkflow').hidden = domain !== 'steps';
  $('#exportSteps').hidden = domain !== 'steps';
  $('#addRetailPdf').hidden = domain !== 'retail';
  document.querySelectorAll('#domains button').forEach(button => button.classList.toggle('active', button.dataset.domain === domain));
  $('#areaTitle').textContent = state.domainLabels?.[domain] || document.querySelector(`[data-domain="${domain}"]`).textContent;
  const result = await api(`/api/data/${domain}/files`);
  if (requestId !== state.requestId || domain !== state.domain) return;
  state.files = result.files; state.topicLabels = result.topic_labels || {}; state.retailCollectionRoles = result.collection_roles || {}; state.retailCollectionLabels = result.collection_labels || {}; renderFiles();
}

async function showHistory() {
  const requestId = ++state.requestId; state.domain = 'history'; rememberTab('history'); state.selected = null;
  $('#editor').hidden = true; $('#empty').hidden = true; $('#historyPage').hidden = false; $('#milvusPage').hidden = true;
  document.querySelector('main>aside').hidden = true; $('main').classList.add('history-mode');
  document.querySelectorAll('#domains button').forEach(button => button.classList.toggle('active', button.id === 'historyNav'));
  $('#historyList').innerHTML = '<div class="history-empty">Loading history…</div>';
  try {
    const domains = ['steps', 'retail'].filter(domain => state.me.roles.includes(domain));
    const [historyResults, fileResults] = await Promise.all([
      Promise.all(domains.map(domain => api(`/api/history/${domain}`))),
      state.me.approver ? Promise.all(domains.map(domain => api(`/api/data/${domain}/files`))) : Promise.resolve(domains.map(() => ({files: []}))),
    ]);
    if (requestId !== state.requestId) return;
    state.historyVersions = historyResults.flatMap(result => result.versions).sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    state.historyPending = domains
      .flatMap((domain, index) => (fileResults[index]?.files || []).filter(file => file.pending).map(file => ({...file, domain})))
      .sort((a, b) => new Date(b.pending.submitted_at) - new Date(a.pending.submitted_at));
    if (!state.me.approver || !state.historyPending.length) state.historyView = 'approved';
    renderHistory();
  } catch (error) { $('#historyList').innerHTML = `<div class="notice error">${esc(error.message)}</div>`; }
}

function showConnectedSystems() {
  ++state.requestId; state.domain = 'connected_systems'; rememberTab('connected_systems'); state.selected = null;
  $('#editor').hidden = true; $('#empty').hidden = true; $('#historyPage').hidden = true; $('#milvusPage').hidden = false;
  document.querySelector('main>aside').hidden = true; $('main').classList.add('history-mode');
  document.querySelectorAll('#domains button').forEach(button => button.classList.toggle('active', button.id === 'connectedSystemsNav'));
  $('#milvusContent').innerHTML = `<div class="connected-systems-head"><p class="eyebrow">INTEGRATIONS</p><h1>Connected systems</h1><p>Manage the systems that receive or expose approved Data Studio content.</p></div><div class="connected-systems-grid"><button class="connected-system-card" id="openMilvusSystem"><span class="connected-system-icon">M</span><span><strong>Milvus</strong><small>Vector database · Collections and ingestion</small></span><b>Open →</b></button><article class="connected-system-placeholder"><span class="connected-placeholder-icon">+</span><span class="connected-placeholder-copy"><strong>More systems</strong><small>Additional integrations will appear here.</small></span></article></div>`;
  $('#openMilvusSystem').onclick = showMilvus;
}

async function showMilvus() {
  const requestId = ++state.requestId; state.domain = 'milvus'; rememberTab('milvus'); state.selected = null;
  $('#editor').hidden = true; $('#empty').hidden = true; $('#historyPage').hidden = true; $('#milvusPage').hidden = false;
  document.querySelector('main>aside').hidden = true; $('main').classList.add('history-mode');
  document.querySelectorAll('#domains button').forEach(button => button.classList.toggle('active', button.id === 'connectedSystemsNav'));
  $('#milvusContent').innerHTML = '<div class="milvus-loading">Loading Milvus status…</div>';
  try {
    const status = await api('/api/milvus/status'); if (requestId !== state.requestId) return;
    renderMilvus(status);
  } catch (error) { $('#milvusContent').innerHTML = `<div class="notice error">${esc(error.message)}</div>`; }
}

function renderMilvus(status) {
  const events = Array.isArray(status.events) ? status.events : [];
  const backgroundJobs = Array.isArray(status.background_jobs) ? status.background_jobs : [];
  const activeJobs = backgroundJobs.filter(job => ['queued', 'embedding', 'retrying'].includes(job.status));
  const serverCollections = Array.isArray(status.server_collections) ? status.server_collections : [];
  const inventory = Array.isArray(status.chunk_inventory) ? status.chunk_inventory : [];
  const totalChunks = inventory.reduce((sum, item) => sum + Number(item.chunks || 0), 0);
  const unapprovedChunks = inventory.filter(item => !item.approved).reduce((sum, item) => sum + Number(item.chunks || 0), 0);
  const chunkRows = inventory.map((item, index) => `<tr class="milvus-source-row ${item.approved ? '' : 'milvus-unapproved'}" data-domain="${esc(item.domain)}" data-status="${item.approved ? 'approved' : 'unapproved'}" data-search="${esc(`${item.title} ${item.path} ${item.collection || ''}`.toLowerCase())}"><td><input type="checkbox" class="milvus-chunk-select" data-index="${index}" ${item.error || !item.collection ? 'disabled' : ''}></td><td><strong>${esc(item.title)}</strong><small>${esc(item.path)}</small></td><td>${esc(item.domain)}</td><td><strong>${esc(item.chunks)}</strong></td><td><span class="milvus-approval ${item.approved ? 'approved' : 'pending'}">${item.approved ? 'Approved' : 'Not approved'}</span></td><td>${esc(item.collection || 'Not routed')}${item.error ? `<small class="milvus-error">${esc(item.error)}</small>` : ''}</td></tr>`).join('');
  const renderServerRows = entries => entries.map(entry => {
    const vectors = Array.isArray(entry.vectors) && entry.vectors.length ? entry.vectors.map(vector => `${esc(vector.field)} · ${esc(vector.dimension)}-dim`).join('<br>') : 'No vector field';
    return `<tr><td><strong>${esc(entry.name)}</strong>${entry.managed ? '<small><span class="milvus-managed">Managed by Data Studio</span></small>' : ''}</td><td>${entry.error ? '—' : esc(entry.row_count)}</td><td>${vectors}</td><td>${entry.error ? '—' : esc(entry.field_count)}</td><td>${esc(entry.primary_field || '—')}</td><td>${esc(entry.load_state || 'Unknown')}${entry.error ? `<small class="milvus-error">${esc(entry.error)}</small>` : ''}</td><td><button class="milvus-delete-collection" data-delete-collection="${esc(entry.name)}">Delete</button></td></tr>`;
  }).join('');
  const managedCollections = serverCollections.filter(entry => entry.managed), unrelatedCollections = serverCollections.filter(entry => !entry.managed);
  const collectionTable = entries => `<div class="milvus-table-wrap"><table><thead><tr><th>Collection</th><th>Rows</th><th>Vector fields</th><th>Fields</th><th>Primary key</th><th>Load state</th><th></th></tr></thead><tbody>${renderServerRows(entries)}</tbody></table></div>`;
  const serverSection = `<section class="milvus-server"><div class="milvus-server-head"><div><p class="eyebrow">SERVER EXPLORER</p><h2>Application collections</h2><p>Collections used by Data Studio on ${esc(status.host)}:${esc(status.port)}.</p></div><strong>${status.connected ? `${managedCollections.length} of ${serverCollections.length}` : 'Unavailable'}</strong></div>${status.connection_error ? `<div class="notice error">Could not inspect the Milvus server: ${esc(status.connection_error)}</div>` : `${managedCollections.length ? collectionTable(managedCollections) : '<div class="no-results">No application collections exist on this server yet.</div>'}<details class="milvus-other-collections"><summary><span>Other server collections</span><small>${unrelatedCollections.length} unrelated collection${unrelatedCollections.length === 1 ? '' : 's'}</small></summary>${unrelatedCollections.length ? collectionTable(unrelatedCollections) : '<div class="no-results">No unrelated collections found.</div>'}</details>`}</section>`;
  const eventGroups = events.reduce((groups, event) => { const name = event.collection || 'Unassigned'; (groups[name] ||= []).push(event); return groups; }, {});
  const activityGroups = Object.entries(eventGroups).sort(([a], [b]) => a.localeCompare(b)).map(([collection, collectionEvents]) => {
    const failures = collectionEvents.filter(event => event.status !== 'synced').length;
    const rows = collectionEvents.map(event => `<tr><td><span class="milvus-state ${esc(event.status)}">${esc(event.status)}</span></td><td>${esc(event.action.replaceAll('_', ' '))}</td><td><strong>${esc(event.source_path)}</strong><small>${esc(event.domain)}</small></td><td>${esc(event.chunks)}</td><td>${esc(event.user)}</td><td>${new Date(event.at).toLocaleString()}${event.error ? `<small class="milvus-error">${esc(event.error)}</small>` : ''}</td></tr>`).join('');
    return `<details class="milvus-activity-group"><summary><span><strong>${esc(collection)}</strong><small>${collectionEvents.length} attempt${collectionEvents.length === 1 ? '' : 's'}</small></span><span class="milvus-activity-counts"><b>${collectionEvents.length - failures} synced</b>${failures ? `<b class="failed">${failures} failed</b>` : ''}</span></summary><div class="milvus-table-wrap"><table><thead><tr><th>Status</th><th>Action</th><th>Source</th><th>Chunks</th><th>User</th><th>Time</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
  }).join('');
  const backgroundRows = backgroundJobs.map(job => `<tr><td><span class="milvus-state ${esc(job.status)}">${esc(job.status)}</span></td><td><strong>${esc(job.source_path)}</strong><small>${esc(job.domain)} · ${esc(job.version_id)}</small></td><td>${esc(job.attempts)} / 3</td><td>${esc(job.requested_by)}</td><td>${new Date(job.updated_at).toLocaleString()}${job.error ? `<small class="milvus-error">${esc(job.error)}</small>` : ''}</td></tr>`).join('');
  const backgroundSection = `<details class="milvus-activity" ${activeJobs.length ? 'open' : ''}><summary>Background synchronization <span>${activeJobs.length} active</span></summary><div class="milvus-table-wrap"><table><thead><tr><th>Status</th><th>Source</th><th>Attempts</th><th>User</th><th>Updated</th></tr></thead><tbody>${backgroundRows || '<tr><td colspan="5" class="no-results">No background synchronization jobs yet.</td></tr>'}</tbody></table></div></details>`;
  $('#milvusContent').innerHTML = `<div class="milvus-title"><div><p class="eyebrow">CONNECTED SYSTEMS · MILVUS</p><h1>Server & ingestion</h1></div><div class="milvus-title-actions"><button class="quiet" id="backToConnectedSystems">← All systems</button><button class="quiet" id="refreshMilvus">Refresh</button></div></div>${status.enabled ? '' : '<div class="notice warning">Server browsing is available, but synchronization writes are disabled.</div>'}<div class="milvus-overview"><div><span>Server</span><strong class="${status.connected ? 'milvus-ok' : 'milvus-bad'}">${status.connected ? 'Connected' : 'Unavailable'}</strong><small>${esc(status.host)}:${esc(status.port)}</small></div><div><span>Collections</span><strong>${status.connected ? esc(serverCollections.length) : '—'}</strong><small>Visible on this server</small></div><div><span>Ready to ingest</span><strong>${esc(totalChunks)} chunks</strong><small>${esc(inventory.length)} sources · ${esc(unapprovedChunks)} not approved</small></div><div><span>Background sync</span><strong>${esc(activeJobs.length)} active</strong><small>${esc(backgroundJobs.length)} recent jobs</small></div></div>${serverSection}<details class="milvus-step milvus-select-step" open><summary class="milvus-step-head"><span>1</span><div><h2>Select data</h2><p>Choose the sources to ingest. Business approval and Milvus ingestion are tracked separately.</p></div><div class="milvus-step-actions"><button type="button" class="quiet" id="selectAllChunks">Select all</button><i class="milvus-step-chevron">⌄</i></div></summary><div id="milvusSelectDataBody"><div class="milvus-source-tools"><input id="milvusSourceSearch" placeholder="Search sources or collections"><select id="milvusDomainFilter"><option value="all">All data</option><option value="steps">Steps</option><option value="retail">Retail</option></select><select id="milvusStatusFilter"><option value="all">Any status</option><option value="approved">Approved</option><option value="unapproved">Not approved</option></select><small id="milvusVisibleCount"></small></div><div class="milvus-table-wrap milvus-source-list"><table><thead><tr><th></th><th>Source</th><th>Domain</th><th>Chunks</th><th>Status</th><th>Collection</th></tr></thead><tbody>${chunkRows || '<tr><td colspan="6" class="no-results">No chunkable sources found.</td></tr>'}</tbody></table></div></div></details><section class="milvus-step milvus-ingest-step"><div class="milvus-step-head"><span>2</span><div><h2>Ingest selection</h2><p>Unapproved sources use an audited admin override and remain unapproved.</p></div><button id="ingestSelected" disabled>Ingest selected</button></div><div id="milvusSelectionSummary" class="milvus-selection-summary">No sources selected</div></section>${backgroundSection}<details class="milvus-activity"><summary>Ingestion activity by collection <span>${events.length}</span></summary><div class="milvus-activity-groups">${activityGroups || '<div class="no-results">No synchronization activity yet.</div>'}</div></details><div id="milvusIngestOverlay" class="milvus-ingest-overlay" hidden><div><span class="milvus-ingest-spinner"></span><strong>Ingesting into Milvus…</strong><p id="milvusIngestProgress">Preparing selected chunks</p><small>Keep this page open until ingestion finishes.</small></div></div>`;
  const updateSelection = () => {
    const selected = [...document.querySelectorAll('.milvus-chunk-select:checked')].map(box => inventory[Number(box.dataset.index)]);
    const chunks = selected.reduce((sum, item) => sum + Number(item.chunks || 0), 0);
    const overrides = selected.filter(item => !item.approved).length;
    $('#ingestSelected').disabled = !status.enabled || !selected.length;
    $('#milvusSelectionSummary').textContent = selected.length ? `${selected.length} source${selected.length === 1 ? '' : 's'} · ${chunks} chunks${overrides ? ` · ${overrides} admin override${overrides === 1 ? '' : 's'}` : ''}` : 'No chunk sets selected';
  };
  const filterSources = () => {
    const query = $('#milvusSourceSearch').value.trim().toLowerCase(), domain = $('#milvusDomainFilter').value, approval = $('#milvusStatusFilter').value;
    let visible = 0;
    document.querySelectorAll('.milvus-source-row').forEach(row => { const show = (!query || row.dataset.search.includes(query)) && (domain === 'all' || row.dataset.domain === domain) && (approval === 'all' || row.dataset.status === approval); row.hidden = !show; if (show) visible++; });
    $('#milvusVisibleCount').textContent = `${visible} shown`;
  };
  document.querySelectorAll('.milvus-chunk-select').forEach(box => box.onchange = updateSelection);
  ['milvusSourceSearch','milvusDomainFilter','milvusStatusFilter'].forEach(id => $(`#${id}`).oninput = filterSources);
  filterSources();
  if ($('#selectAllChunks')) $('#selectAllChunks').onclick = event => { event.preventDefault(); event.stopPropagation(); const boxes = [...document.querySelectorAll('.milvus-chunk-select:not(:disabled)')]; const select = boxes.some(box => !box.checked); boxes.forEach(box => box.checked = select); $('#selectAllChunks').textContent = select ? 'Clear all' : 'Select all'; updateSelection(); };
  if ($('#ingestSelected')) $('#ingestSelected').onclick = () => ingestSelectedMilvus(inventory);
  if ($('#refreshMilvus')) $('#refreshMilvus').onclick = showMilvus;
  if ($('#backToConnectedSystems')) $('#backToConnectedSystems').onclick = showConnectedSystems;
  document.querySelectorAll('[data-delete-collection]').forEach(button => button.onclick = () => deleteMilvusCollection(button.dataset.deleteCollection));
}

async function deleteMilvusCollection(collection) {
  const confirmation = prompt(`Delete Milvus collection "${collection}" and all of its rows?\n\nType the exact collection name to confirm:`);
  if (confirmation === null) return;
  if (confirmation !== collection) { toast('Collection name did not match. Nothing was deleted.'); return; }
  const button = document.querySelector(`[data-delete-collection="${CSS.escape(collection)}"]`);
  if (button) { button.disabled = true; button.textContent = 'Deleting…'; }
  try {
    const result = await api(`/api/milvus/collection?name=${encodeURIComponent(collection)}`, {method:'DELETE'});
    toast(result.message); await showMilvus();
  } catch (error) { toast(error.message); if (button) { button.disabled = false; button.textContent = 'Delete'; } }
}

async function ingestSelectedMilvus(inventory) {
  const selected = [...document.querySelectorAll('.milvus-chunk-select:checked')].map(box => inventory[Number(box.dataset.index)]);
  const overrides = selected.filter(item => !item.approved).length;
  if (!selected.length) return;
  const warning = overrides ? ` ${overrides} selected source${overrides === 1 ? '' : 's'} are not business-approved and will be ingested using the admin override.` : '';
  if (!confirm(`Ingest ${selected.length} selected chunk set${selected.length === 1 ? '' : 's'} into Milvus?${warning}`)) return;
  const chunkCount = selected.reduce((sum, item) => sum + Number(item.chunks || 0), 0);
  const button = $('#ingestSelected'), overlay = $('#milvusIngestOverlay');
  button.disabled = true; button.textContent = 'Ingesting…';
  $('#milvusIngestProgress').textContent = `${selected.length} source${selected.length === 1 ? '' : 's'} · ${chunkCount} chunks`;
  overlay.hidden = false;
  try {
    const items = selected.map(({domain, path, version}) => ({domain, path, version}));
    const result = await api('/api/milvus/ingest-selected', {method:'POST', body:JSON.stringify({items})});
    toast(result.failed ? `Ingestion completed with ${result.failed} failure${result.failed === 1 ? '' : 's'}` : `${result.sources} selected source${result.sources === 1 ? '' : 's'} ingested`);
    await showMilvus();
  } catch (error) { overlay.hidden = true; toast(error.message); button.disabled = false; button.textContent = 'Ingest selected'; }
}

async function recreateMilvusCollection(collection) {
  if (!confirm(`Create "${collection}" if it does not exist, or drop and recreate it if it does? Its approved data will then be ingested into the new schema.`)) return;
  try {
    const result = await api('/api/milvus/recreate-collection', {method: 'POST', body: JSON.stringify({collection})});
    toast(result.message);
    await showMilvus();
  } catch (error) { toast(error.message); }
}

async function ingestAllMilvus() {
  const button = $('#ingestAll');
  if (!confirm('Ingest all approved data into Milvus now? This may take several minutes.')) return;
  button.disabled = true; button.textContent = 'Ingesting approved data…';
  try {
    const result = await api('/api/milvus/ingest-all', {method:'POST', body:'{}'});
    toast(result.failed ? `Ingestion completed with ${result.failed} failures` : `${result.sources} sources ingested`);
    await showMilvus();
  } catch (error) { toast(error.message); button.disabled = false; button.textContent = 'Ingest all approved data'; }
}

function renderHistoryToolbar() {
  $('#historyViewToggle').hidden = !state.me.approver;
  if (state.me.approver) {
    $('#historyViewToggle').innerHTML = `<button data-view="approved" class="${state.historyView === 'approved' ? 'active' : ''}">Approved history</button><button data-view="pending" class="${state.historyView === 'pending' ? 'active' : ''}">Pending approval${state.historyPending.length ? ` (${state.historyPending.length})` : ''}</button>`;
    document.querySelectorAll('#historyViewToggle button').forEach(button => button.onclick = () => { state.historyView = button.dataset.view; renderHistory(); });
  }
  const pending = state.historyView === 'pending';
  $('#historyActionFilter').hidden = pending; $('#historyGroupToggle').hidden = pending;
  if (pending) return;
  const actions = [...new Set(state.historyVersions.map(version => version.action))].sort();
  $('#historyActionFilter').innerHTML = ['all', ...actions].map(action =>
    `<button data-action="${action}" class="${state.historyAction === action ? 'active' : ''}">${action === 'all' ? 'All' : esc(action)}</button>`).join('');
  document.querySelectorAll('#historyActionFilter button').forEach(button => button.onclick = () => { state.historyAction = button.dataset.action; renderHistory(); });
  document.querySelectorAll('#historyGroupToggle button').forEach(button => {
    button.classList.toggle('active', state.historyGroupBy === button.dataset.group);
    button.onclick = () => { state.historyGroupBy = button.dataset.group; renderHistory(); };
  });
}

function historyPendingMatchesFilter(item) {
  const query = state.historySearch.trim().toLowerCase();
  if (!query) return true;
  return [item.title, item.name, item.path, item.pending?.submitted_by].some(field => String(field || '').toLowerCase().includes(query));
}

function renderHistoryPending() {
  const items = state.historyPending.filter(historyPendingMatchesFilter);
  if (!items.length) {
    $('#historyList').innerHTML = `<div class="history-empty"><h2>${state.historyPending.length ? 'No matches' : 'Nothing waiting on you'}</h2><p>${state.historyPending.length ? 'No pending changes match your search.' : 'Submitted changes that need approval will appear here.'}</p></div>`;
    return;
  }
  const card = item => `<article class="history-card history-pending-card"><div class="history-version"><span>⏳</span></div><div class="history-details"><div><h3>${esc(item.title || item.name)}</h3><p>${esc(item.path)}</p></div><strong>Awaiting approval</strong><div class="history-meta"><span>Submitted by ${esc(item.pending.submitted_by)}</span><span>${new Date(item.pending.submitted_at).toLocaleString()}</span><span class="history-action">${esc(item.domain)}</span></div></div><div class="history-actions"><button data-open-pending="${esc(item.path)}" data-pending-domain="${esc(item.domain)}">Review</button></div></article>`;
  $('#historyList').innerHTML = items.map(card).join('');
  document.querySelectorAll('[data-open-pending]').forEach(button => button.onclick = async () => {
    await selectDomain(button.dataset.pendingDomain); await openFile(button.dataset.openPending);
  });
}

function historyRemainingLabel(version) {
  const deadline = new Date(version.created_at).getTime() + (version.action_window_hours || 48) * 3600000;
  const remainingMs = deadline - Date.now();
  if (remainingMs <= 0) return null;
  const hours = Math.floor(remainingMs / 3600000);
  if (hours >= 1) return {text: `${hours}h left to undo`, urgent: hours < 6};
  return {text: `${Math.max(1, Math.floor(remainingMs / 60000))}m left to undo`, urgent: true};
}

function historyMatchesFilter(version) {
  if (state.historyAction !== 'all' && version.action !== state.historyAction) return false;
  const query = state.historySearch.trim().toLowerCase();
  if (!query) return true;
  return [version.title, version.source_path, version.summary, version.submitted_by, version.approved_by]
    .some(field => String(field || '').toLowerCase().includes(query));
}

function historyEmptyMessage() {
  return state.historyAction !== 'all' || state.historySearch.trim() ? 'No activity matches your filters.' : 'No actions yet.';
}

function renderHistoryByDocument(items, card) {
  if (!items.length) return `<div class="history-group-empty">${historyEmptyMessage()}</div>`;
  const groups = new Map();
  items.forEach(version => { const key = version.source_path; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(version); });
  return [...groups.entries()].map(([path, versions]) => {
    const latest = versions[0];
    return `<section class="history-domain history-doc-group collapsed"><button class="history-domain-toggle" data-history-domain="${esc(path)}" aria-expanded="false"><h2>${esc(latest.title || path)}<span class="history-domain-count">${versions.length} version${versions.length === 1 ? '' : 's'}</span></h2><span aria-hidden="true">⌄</span></button><div class="history-domain-items">${versions.map(card).join('')}</div></section>`;
  }).join('');
}

function renderHistory() {
  renderHistoryToolbar();
  if (state.historyView === 'pending') { renderHistoryPending(); return; }
  if (!state.historyVersions.length) { $('#historyList').innerHTML = '<div class="history-empty"><h2>No approved versions yet</h2><p>Your approved actions will appear here.</p></div>'; return; }
  const isOwnVersion = version => [version.submitted_by, version.approved_by].some(name => String(name || '').toLowerCase() === String(state.me.username || '').toLowerCase());
  const card = version => {
    const own = isOwnVersion(version); const revoke = version.undo_kind === 'revoke_approval'; const label = revoke ? 'Undo approval' : version.action === 'deleted' ? (own ? 'Undo delete' : 'Restore product') : (own ? 'Undo' : 'Restore');
    let action;
    if (version.action_available) {
      const remaining = historyRemainingLabel(version);
      action = `<div class="history-action-group"><button data-restore-version="${esc(version.id)}" data-version-domain="${esc(version.domain || 'steps')}" data-own="${own}" data-undo-kind="${esc(version.undo_kind || 'restore_version')}">${label}</button>${remaining ? `<small class="history-window-remaining ${remaining.urgent ? 'urgent' : ''}">${esc(remaining.text)}</small>` : ''}</div>`;
    } else action = '<span class="history-read-only" title="The 48-hour action window has expired">Read-only</span>';
    return `<article class="history-card"><div class="history-version"><span>v${version.version}</span><i></i></div><div class="history-details"><div><h3>${esc(version.title || version.source_path)}</h3><p>${esc(version.source_path)}</p></div><strong>${esc(version.summary)}</strong><div class="history-meta"><span>${new Date(version.created_at).toLocaleString()}</span><span>Submitted by ${esc(version.submitted_by)}</span><span>Approved by ${esc(version.approved_by)}</span><span class="history-action">${esc(version.action)}</span></div></div><div class="history-actions"><button class="secondary" data-view-version="${esc(version.id)}" data-version-domain="${esc(version.domain || 'steps')}">View</button>${action}</div></article>`;
  };
  const filtered = state.historyVersions.filter(historyMatchesFilter);
  const renderGroup = items => state.historyGroupBy === 'document' ? renderHistoryByDocument(items, card) : (items.length ? items.map(card).join('') : `<div class="history-group-empty">${historyEmptyMessage()}</div>`);
  if (state.me.admin) {
    const section = (domain, title) => { const items = filtered.filter(version => version.domain === domain); return `<section class="history-domain"><button class="history-domain-toggle" data-history-domain="${domain}" aria-expanded="true"><h2>${title}<span class="history-domain-count">${items.length}</span></h2><span aria-hidden="true">⌄</span></button><div class="history-domain-items">${renderGroup(items)}</div></section>`; };
    $('#historyList').innerHTML = section('steps', 'Steps & workflows activity') + section('retail', 'Retail products activity');
  } else $('#historyList').innerHTML = renderGroup(filtered);
  document.querySelectorAll('[data-history-domain]').forEach(button => button.onclick = () => {
    const section = button.closest('.history-domain'); const closed = section.classList.toggle('collapsed');
    button.setAttribute('aria-expanded', String(!closed));
  });
  document.querySelectorAll('[data-view-version]').forEach(button => button.onclick = () => viewVersion(button.dataset.versionDomain, button.dataset.viewVersion));
  document.querySelectorAll('[data-restore-version]').forEach(button => button.onclick = () => restoreVersion(button.dataset.versionDomain, button.dataset.restoreVersion, button.dataset.own === 'true', button.dataset.undoKind));
}

async function viewVersion(domain, id) {
  try {
    const result = await api(`/api/history/${domain}/version?id=${encodeURIComponent(id)}`);
    $('#versionTitle').textContent = `Version ${result.meta.version}: Before and after`;
    $('#versionMeta').textContent = `${result.meta.source_path} · ${new Date(result.meta.created_at).toLocaleString()} · ${result.meta.summary}`;
    $('#versionContent').innerHTML = renderVersionComparison(domain, result.before, result.data, result.before_meta, result.meta);
    $('#versionDialog').showModal();
  } catch (error) { toast(error.message); }
}

function comparisonTable(section) {
  const rows = tableRows(section);
  return `<div class="comparison-table-scroll"><table class="comparison-table"><tbody>${rows.map((row, rowIndex) => `<tr>${row.map(cell => `<${rowIndex === 0 ? 'th' : 'td'}>${esc(cell)}</${rowIndex === 0 ? 'th' : 'td'}>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function comparisonSection(section, missingText) {
  if (!section) return `<div class="comparison-missing">${esc(missingText)}</div>`;
  const title = shortSectionName(section, 0);
  const content = section.variant === 'table' ? comparisonTable(section) : `<div class="comparison-text">${esc(section.content || 'No content')}</div>`;
  return `<div class="comparison-section"><strong>${esc(title)}</strong>${content}</div>`;
}

function comparableData(value) {
  if (Array.isArray(value)) return value.map(comparableData);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, comparableData(value[key])]));
  return value;
}
function sameData(left, right) { return JSON.stringify(comparableData(left)) === JSON.stringify(comparableData(right)); }

function renderRetailComparison(before, after) {
  const oldSections = before?.sections || [], newSections = after?.sections || [];
  const changed = [];
  for (let index = 0; index < Math.max(oldSections.length, newSections.length); index++) {
    const oldSection = oldSections[index], newSection = newSections[index];
    if (!sameData(oldSection, newSection)) changed.push({index, oldSection, newSection});
  }
  const titleChange = before && before.title !== after?.title ? `<article class="comparison-change"><div class="comparison-change-title"><span>Document name changed</span></div><div class="before-after"><section><h3>Before</h3><div class="comparison-text">${esc(before.title || 'Untitled')}</div></section><section><h3>After</h3><div class="comparison-text">${esc(after.title || 'Untitled')}</div></section></div></article>` : '';
  if (!changed.length && !titleChange) return '<div class="comparison-empty">No content differences were found from the previous version.</div>';
  return titleChange + changed.map(change => `<article class="comparison-change"><div class="comparison-change-title"><span>Changed section ${change.index + 1}</span><strong>${esc(shortSectionName(change.newSection || change.oldSection, change.index))}</strong></div><div class="before-after"><section><h3>Before</h3>${comparisonSection(change.oldSection, 'This section did not exist.')}</section><section><h3>After</h3>${comparisonSection(change.newSection, 'This section was deleted.')}</section></div></article>`).join('');
}

function workflowEntries(data) {
  if (Array.isArray(data?.steps)) return data.steps;
  if (Array.isArray(data)) return data;
  return data ? [data] : [];
}

function comparisonEntry(entry, missingText) {
  if (!entry) return `<div class="comparison-missing">${esc(missingText)}</div>`;
  const label = entry.Note ?? entry.step ?? entry.title ?? entry.section_title ?? 'Entry';
  const text = entry.text ?? entry.content ?? Object.entries(entry).filter(([, value]) => typeof value !== 'object').map(([key, value]) => `${key}: ${value}`).join('\n');
  return `<div class="comparison-section"><strong>${esc(label)}</strong><div class="comparison-text">${esc(text || 'No text')}</div></div>`;
}

function renderWorkflowComparison(before, after) {
  const oldEntries = workflowEntries(before), newEntries = workflowEntries(after), changed = [];
  for (let index = 0; index < Math.max(oldEntries.length, newEntries.length); index++) if (!sameData(oldEntries[index], newEntries[index])) changed.push({index, oldEntry:oldEntries[index], newEntry:newEntries[index]});
  const titleChange = before && before.title !== after?.title ? `<article class="comparison-change"><div class="comparison-change-title"><span>Workflow name changed</span></div><div class="before-after"><section><h3>Before</h3><div class="comparison-text">${esc(before.title || 'Untitled')}</div></section><section><h3>After</h3><div class="comparison-text">${esc(after.title || 'Untitled')}</div></section></div></article>` : '';
  if (!changed.length && !titleChange && sameData(before, after)) return '<div class="comparison-empty">No content differences were found from the previous version.</div>';
  return titleChange + changed.map(change => `<article class="comparison-change"><div class="comparison-change-title"><span>Changed entry ${change.index + 1}</span></div><div class="before-after"><section><h3>Before</h3>${comparisonEntry(change.oldEntry, 'This entry did not exist.')}</section><section><h3>After</h3>${comparisonEntry(change.newEntry, 'This entry was deleted.')}</section></div></article>`).join('');
}

function renderVersionComparison(domain, before, after, beforeMeta, meta) {
  if (meta.action === 'deleted') return `<div class="comparison-baseline"><strong>Full document deleted</strong><p>This history entry preserves the last structured data for audit purposes. The original product is no longer in the active library.</p></div>`;
  if (!before) return `<div class="comparison-baseline"><strong>Original baseline</strong><p>Version ${meta.version} is the first saved version, so there is no earlier version to compare.</p>${domain === 'retail' ? renderRetailComparison({sections:[]}, after) : renderWorkflowComparison(null, after)}</div>`;
  const heading = `<div class="comparison-legend"><span><i class="before-dot"></i>Before · Version ${beforeMeta.version}</span><span><i class="after-dot"></i>After · Version ${meta.version}</span></div>`;
  return heading + (domain === 'retail' ? renderRetailComparison(before, after) : renderWorkflowComparison(before, after));
}

async function restoreVersion(domain, id, own, undoKind) {
  const message = undoKind === 'revoke_approval'
    ? 'Undo this approval? This will mark the source as not approved and remove its data from connected systems. The Milvus collection will be dropped only if it becomes empty.'
    : `${own ? 'Undo this change' : 'Restore this version'}? It will immediately update Data Studio and synchronize Milvus without another approval.`;
  if (!confirm(message)) return;
  try { const result = await api(`/api/history/${domain}/restore`, {method:'POST', body:JSON.stringify({id})}); toast(result.message); await selectDomain(domain); } catch (error) { toast(error.message); }
}

function renderFiles() {
  const query = $('#search').value.trim().toLowerCase();
  const files = state.files.filter(file => !query || file.path.toLowerCase().includes(query) || String(file.title || '').toLowerCase().includes(query));
  $('#count').textContent = files.length;
  const root = {folders: {}, files: []};
  for (const file of files) {
    const parts = file.path.split('/'); let node = root;
    for (const folder of parts.slice(0, -1)) node = node.folders[folder] ||= {folders: {}, files: []};
    node.files.push(file);
  }
  const validationItems = state.domain === 'steps' ? files.filter(file => file.validation) : [];
  const completeWorkflows = validationItems.filter(file => file.validation.complete).length;
  const reviewedFields = validationItems.reduce((total, file) => total + file.validation.reviewed, 0);
  const totalFields = validationItems.reduce((total, file) => total + file.validation.total, 0);
  const validationOverview = validationItems.length ? `<div class="library-validation ${completeWorkflows === validationItems.length ? 'complete' : ''}"><strong>${completeWorkflows} of ${validationItems.length} workflows validated</strong><span>${reviewedFields} of ${totalFields} fields reviewed</span></div>` : '';
  if (state.domain === 'steps') {
    const topics = Object.entries(root.folders).sort(([a], [b]) => a.localeCompare(b));
    if (state.activeTopic && !root.folders[state.activeTopic] && state.newWorkflowDraft?.topic !== state.activeTopic) state.activeTopic = null;
    const topicList = topics.map(([name, child]) => `<button class="main-topic-link ${state.activeTopic === name ? 'active' : ''}" data-main-topic="${esc(name)}"><span>${esc(name)}</span><small>${countTreeFiles(child)}</small></button>`).join('');
    $('#files').innerHTML = validationOverview + (topicList || '<p class="no-results">No matching topics.</p>');
    document.querySelectorAll('[data-main-topic]').forEach(button => button.onclick = () => {
      state.activeTopic = button.dataset.mainTopic;
      state.topicBrowsePath = '';
      state.selected = null;
      rememberTopic('steps', state.activeTopic);
      forgetSelection('steps');
      $('#editor').hidden = true;
      $('#empty').hidden = false;
      renderFiles();
    });
    if (!state.selected) renderTopicExplorer(root);
    return;
  }
  const retailFocusBar = state.domain === 'retail' && state.retailCollection && state.selected && !query ? `<button class="retail-sidebar-unfocus" id="retailSidebarUnfocus">‹ All retail products</button>` : '';
  $('#files').innerHTML = retailFocusBar + validationOverview + (renderTree(root, '', 0, Boolean(query)) || '<p class="no-results">No matching data.</p>');
  wireTreeControls('#files', 'sidebar');
  if ($('#retailSidebarUnfocus')) $('#retailSidebarUnfocus').onclick = () => { state.retailCollection = null; rememberRetailCollection(null); selectDomain('retail'); };
}

function renderTree(node, parentPath, depth, searchOpen, mode = 'sidebar') {
  const folders = Object.entries(node.folders).sort(([a, left], [b, right]) => {
    const leftHasSubtopics = Object.keys(left.folders).length > 0;
    const rightHasSubtopics = Object.keys(right.folders).length > 0;
    return Number(rightHasSubtopics) - Number(leftHasSubtopics) || a.localeCompare(b);
  });
  let retailSidebarColorIndex = 0;
  const folderHtml = folders.map(([name, child]) => {
    const path = parentPath ? `${parentPath}/${name}` : name;
    const childFolders = Object.keys(child.folders);
    if (mode === 'sidebar' && state.domain === 'retail' && depth === 0) {
      if (state.retailCollection && state.selected && state.retailCollection !== path) return '';
      const count = countTreeFiles(child) + (state.retailCollectionRoles[path] === 'product' ? 1 : 0);
      const displayName = state.retailCollectionLabels[path] || name;
      const iconColor = RETAIL_SIDEBAR_PALETTE[retailSidebarColorIndex++ % RETAIL_SIDEBAR_PALETTE.length];
      return `<div class="topic-group retail-folder-card"><button class="file ${state.retailCollection === path ? 'active' : ''}" style="--depth:${depth}" data-folder="${esc(path)}"><span class="file-icon" style="background:${iconColor}">${esc(displayName.slice(0, 1).toUpperCase())}</span><span><strong>${esc(displayName)}</strong><small>${count} ${count === 1 ? 'product' : 'products'}</small></span><b>›</b></button></div>`;
    }
    if (!childFolders.length && child.files.length === 1) {
      const file = child.files[0];
      const displayTitle = file.title || name;
      const status = file.pending ? `${documentTypeLabel(file)} · Pending` : file.validation ? (file.validation.complete ? 'Validated workflow' : `Workflow ${file.validation.reviewed}/${file.validation.total}`) : documentTypeLabel(file);
      if (mode === 'explorer') { const type = documentTypeLabel(file); const icon = type === 'Workflow' ? 'FLOW' : type.toUpperCase().slice(0, 5); return `<button class="file explorer-document ${state.selected?.path === file.path ? 'active' : ''}" style="--depth:${depth}" data-path="${esc(file.path)}"><span class="file-icon">${esc(icon)}</span><span><strong>${esc(displayTitle)}</strong><small class="${file.validation && !file.validation.complete ? 'needs-validation' : ''}">${esc(status)}</small></span><b>›</b></button>`; }
      return `<button class="topic leaf-topic ${state.selected?.path === file.path ? 'active' : ''}" style="--depth:${depth}" data-path="${esc(file.path)}"><span>›</span><strong>${esc(displayTitle)}</strong>${status ? `<small class="${file.validation && !file.validation.complete ? 'needs-validation' : ''}">${esc(status)}</small>` : ''}</button>`;
    }
    const open = searchOpen || state.expanded.has(path);
    const count = countTreeFiles(child);
    const countLabel = mode === 'explorer' ? `${count} ${count === 1 ? 'subtopic' : 'subtopics'}` : String(count);
    const folderAttribute = mode === 'explorer' ? 'data-explorer-folder' : 'data-folder';
    return `<div class="topic-group"><button class="topic ${depth === 0 ? 'main-topic' : ''}" style="--depth:${depth}" ${folderAttribute}="${esc(path)}" aria-expanded="${open}"><span>${open ? '⌄' : '›'}</span><strong>${esc(name)}</strong><small class="topic-child-count">${esc(countLabel)}</small></button>${open ? `<div class="topic-children">${renderTree(child, path, depth + 1, searchOpen, mode)}</div>` : ''}</div>`;
  }).join('');
  const visibleFiles = state.domain === 'retail' && depth === 0 ? (state.retailCollection && state.selected && mode === 'sidebar' ? [] : node.files.filter(file => !Object.keys(node.folders).some(folder => retailNorm(folder) === retailNorm(file.title || file.name.replace(/\.[^.]+$/, ''))))) : node.files;
  const fileHtml = visibleFiles.sort((a, b) => (a.title || a.name).localeCompare(b.title || b.name)).map(file => `<button class="file ${state.selected?.path === file.path ? 'active' : ''}" style="--depth:${depth}" data-path="${esc(file.path)}"><span class="file-icon"${state.domain === 'retail' && depth === 0 ? ` style="background:${retailCardColor(retailSidebarColorIndex++)}"` : ''}>${state.domain === 'retail' ? esc((file.title || file.name).trim().slice(0, 1).toUpperCase() || 'P') : file.name.endsWith('.json') ? 'JSON' : file.name.endsWith('.pdf') ? 'PDF' : file.name.endsWith('.product') ? 'PRD' : 'XLS'}</span><span><strong>${esc(file.title || file.name.replace(/\.[^.]+$/, ''))}</strong><small class="${file.validation && !file.validation.complete ? 'needs-validation' : ''}">${file.pending ? 'Pending approval' : file.validation ? (file.validation.complete ? 'Validated' : `Validation ${file.validation.reviewed}/${file.validation.total}`) : documentTypeLabel(file)}</small></span><b>›</b></button>`).join('');
  return folderHtml + fileHtml;
}

function documentTypeLabel(file) {
  const path = String(file.path || '').toLowerCase();
  const name = String(file.name || '').toLowerCase();
  if (name === 'workflow.json') return 'Workflow';
  if (name === 'tips.json') return 'Tips';
  if (name === 'faq.json') return 'FAQ';
  if (name === 'rules.json') return 'Rules';
  if (name.endsWith('.product')) return 'Product';
  if (name.includes('overview') || path.split('/')[0].includes('overview')) return 'Overview';
  return 'Document';
}

function wireTreeControls(container, mode) {
  document.querySelectorAll(`${container} [data-path]`).forEach(button => button.onclick = () => openFile(button.dataset.path));
  const folderSelector = mode === 'explorer' ? '[data-explorer-folder]' : '[data-folder]';
  document.querySelectorAll(`${container} ${folderSelector}`).forEach(button => button.onclick = () => {
    const path = mode === 'explorer' ? button.dataset.explorerFolder : button.dataset.folder;
    if (!(mode === 'sidebar' && state.domain === 'retail')) { if (state.expanded.has(path)) state.expanded.delete(path); else state.expanded.add(path); }
    if (mode === 'sidebar' && state.domain === 'retail') { state.retailCollection = path; rememberRetailCollection(path); state.selected = null; forgetSelection('retail'); renderRetailCollection(path); }
    if (mode === 'explorer') renderTopicExplorer(); else renderFiles();
  });
  document.querySelectorAll(`${container} [data-retail-export-folder]`).forEach(button => button.onclick = event => { event.stopPropagation(); downloadRetailExport(button.dataset.retailExportFolder, button); });
}

async function downloadRetailExport(path, button) {
  const overlay = $('#exportingOverlay'); const heading = overlay.querySelector('strong'); const original = button.textContent;
  button.disabled = true; heading.textContent = path.includes('.') ? 'Preparing product export…' : 'Preparing product collection…'; overlay.hidden = false;
  try {
    const response = await fetch(`/api/data/retail/export?path=${encodeURIComponent(path)}`);
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || 'Could not prepare the Retail export'); }
    const blob = await response.blob();
    const filename = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(response.headers.get('content-disposition') || '')?.[1] || (path.includes('.') ? 'retail-product.pdf' : 'retail-products.zip');
    const url = URL.createObjectURL(blob); const download = document.createElement('a'); download.href = url; download.download = decodeURIComponent(filename); document.body.append(download); download.click(); download.remove(); URL.revokeObjectURL(url);
    toast(path.includes('.') ? 'Product exported' : 'Product collection exported');
  } catch (error) { showNotice(error.message, 'error'); }
  finally { overlay.hidden = true; heading.textContent = 'Preparing export…'; button.disabled = false; button.textContent = original; }
}

const RETAIL_SIDEBAR_PALETTE = ['#1a56db', '#1e40af', '#075985', '#0369a1', '#0f2554'];
const RETAIL_ICON_PALETTE = ['#0369a1', '#0369a1', '#075985', '#0f2554', '#0f2554', '#075985', '#1e40af', '#1e40af', '#1e40af'];
function retailCardColor(index) { return RETAIL_ICON_PALETTE[index % RETAIL_ICON_PALETTE.length]; }
const RETAIL_ICON_PATHS = {
  car: '<path d="M4 16l1.5-5A2 2 0 0 1 7.4 9.5h9.2A2 2 0 0 1 18.5 11L20 16"/><path d="M3 16h18v3a1 1 0 0 1-1 1h-1.5a1 1 0 0 1-1-1v-1h-11v1a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><circle cx="7.5" cy="16" r="1.3"/><circle cx="16.5" cy="16" r="1.3"/>',
  doctor: '<path d="M9 3v3a3 3 0 0 0 6 0V3"/><path d="M7 6v4a5 5 0 0 0 10 0V6"/><circle cx="18.5" cy="15.5" r="3"/><path d="M17 15.5h3"/>',
  house: '<path d="M4 11l8-6 8 6"/><path d="M6 10v9a1 1 0 0 0 1 1h3v-5h4v5h3a1 1 0 0 0 1-1v-9"/>',
  card: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/><path d="M7 15h4"/>',
  briefcase: '<rect x="3" y="8" width="18" height="11" rx="2"/><path d="M8 8V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M3 13h18"/>',
  doc: '<path d="M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v4h4"/><path d="M9 13h6M9 16h6"/>',
  star: '<path d="M12 3l2.6 5.9 6.4.6-4.8 4.3 1.4 6.2L12 16.9l-5.6 3.1 1.4-6.2L3 9.5l6.4-.6z"/>'
};
function retailIconSvg(key) { return `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${RETAIL_ICON_PATHS[key] || RETAIL_ICON_PATHS.doc}</svg>`; }
function retailCardIcon(file, colorHex) {
  if (file.collectionGeneral) return `<span class="retail-create-icon" style="background:${colorHex}">G</span>`;
  if (file.new) return `<span class="retail-create-icon" style="background:#1e40af">${retailIconSvg('star')}</span>`;
  const title = (file.title || file.name || '').toLowerCase();
  let key = 'doc';
  if (/car/.test(title)) key = 'car';
  else if (/doctor/.test(title)) key = 'doctor';
  else if (/hous/.test(title)) key = 'house';
  else if (/salary|acquisition/.test(title)) key = 'briefcase';
  else if (/card/.test(title)) key = 'card';
  else if (/personal/.test(title)) key = 'card';
  return `<span class="retail-create-icon" style="background:${colorHex}">${retailIconSvg(key)}</span>`;
}
function retailTitleIconSvg(title) {
  const t = (title || '').toLowerCase();
  let key = 'doc';
  if (/\bcar\b/.test(t)) key = 'car';
  else if (/doctor/.test(t)) key = 'doctor';
  else if (/hous/.test(t)) key = 'house';
  else if (/salary|acquisition/.test(t)) key = 'briefcase';
  else if (/card/.test(t)) key = 'card';
  else if (/personal/.test(t)) key = 'card';
  return retailIconSvg(key);
}

function retailCardStatus(file, label) {
  if (file.collectionGeneral) return {text: `Current ${esc(label)} content`, cls: 'status-muted'};
  if (file.new) return {text: 'Draft', cls: 'status-draft'};
  if (file.pending) return {text: 'Pending approval', cls: 'status-pending'};
  return {text: 'Ready to review', cls: 'status-ready'};
}
function renderRetailCollection(path) {
  setRetailJourneyFocus(false);
  const products = state.files.filter(file => file.path.startsWith(`${path}/`));
  const baseName = path.split('/').at(-1); const label = state.retailCollectionLabels[path] || baseName; const singular = retailNorm(baseName) === 'loans' ? 'loan' : 'product';
  const general = state.files.find(file => !file.path.includes('/') && retailNorm(file.title || file.name.replace(/\.[^.]+$/, '')) === retailNorm(baseName));
  const existingIsProduct = state.retailCollectionRoles[path] === 'product';
  const collectionItems = general ? [{...general, collectionGeneral:!existingIsProduct}, ...products] : products;
  const productCount = products.length + (general && existingIsProduct ? 1 : 0);
  $('#editor').hidden = true; $('#empty').hidden = false; $('#empty').className = 'empty retail-collection-page';
  $('#empty').innerHTML = `<div class="retail-page-header"><div><p class="retail-crumb">${esc(state.domain)} /</p><h2>${esc(label)}</h2><p class="muted">${productCount} ${productCount === 1 ? singular : `${singular}s`} available${general && !existingIsProduct ? ' · general information included' : ''}</p></div><div class="retail-collection-actions"><button id="newRetailCollectionProduct">+ New ${esc(singular)}</button><button id="exportRetailCollection" class="secondary">↓ Export all</button><details class="retail-manage-menu"><summary>Manage <span>⌄</span></summary><div><button id="renameRetailCollection" class="secondary">Rename collection</button>${state.me.approver ? '<button id="deleteRetailCollection" class="danger">Delete entire collection</button>' : ''}</div></details></div></div><section class="retail-collection"><header><p class="eyebrow">RETAIL PRODUCTS</p><h3>${esc(label)}</h3></header><div class="retail-collection-grid">${collectionItems.map((file, index) => { const status = retailCardStatus(file, label); return `<button data-collection-product="${esc(file.path)}" class="${file.collectionGeneral ? 'general-product-card' : ''}">${retailCardIcon(file, retailCardColor(index))}<span><strong>${file.collectionGeneral ? 'General information' : esc(file.title || file.name.replace(/\.[^.]+$/, ''))}</strong><small class="${status.cls}">${status.text}</small></span><b>›</b></button>`; }).join('') || '<p>No products in this group yet.</p>'}</div></section>`;
  document.querySelectorAll('[data-collection-product]').forEach(button => button.onclick = () => openFile(button.dataset.collectionProduct));
  $('#exportRetailCollection').onclick = () => downloadRetailExport(path, $('#exportRetailCollection'));
  $('#newRetailCollectionProduct').onclick = () => openRetailProductCreate(path, singular);
  $('#renameRetailCollection').onclick = async () => {
    const title = prompt('Collection name', label); if (title === null || !title.trim() || title.trim() === label) return;
    try { const result = await api('/api/data/retail/collection', {method:'PUT', body:JSON.stringify({path, title:title.trim()})}); state.retailCollectionLabels = result.collection_labels || state.retailCollectionLabels; renderFiles(); renderRetailCollection(path); toast(result.message); } catch (error) { showNotice(error.message, 'error'); }
  };
  if ($('#deleteRetailCollection')) $('#deleteRetailCollection').onclick = async () => {
    if (!confirm(`Delete the complete “${label}” collection? This removes General information and all ${productCount} ${productCount === 1 ? singular : `${singular}s`}. History remains available for recovery.`)) return;
    try { const result = await api(`/api/data/retail/collection?path=${encodeURIComponent(path)}`, {method:'DELETE'}); state.retailCollection = null; rememberRetailCollection(null); await selectDomain('retail'); toast(result.message); } catch (error) { showNotice(error.message, 'error'); }
  };
}

function topicNodeAt(topic, relativePath) {
  let node = topic;
  for (const part of String(relativePath || '').split('/').filter(Boolean)) {
    node = node?.folders?.[part];
    if (!node) return null;
  }
  return node;
}

function renderTopicLevel(node, relativePath) {
  const folders = Object.entries(node.folders || {}).sort(([a], [b]) => a.localeCompare(b)).map(([name, child]) => {
    const path = relativePath ? `${relativePath}/${name}` : name;
    const count = countTreeFiles(child);
    return `<button class="topic topic-drill-row" data-topic-drill="${esc(path)}"><span class="topic-drill-icon">›</span><strong>${esc(name)}</strong><small class="topic-child-count">${count} ${count === 1 ? 'item' : 'items'}</small></button>`;
  }).join('');
  const files = [...(node.files || [])].sort((a, b) => (a.title || a.name).localeCompare(b.title || b.name)).map(file => {
    const type = documentTypeLabel(file); const icon = type === 'Workflow' ? 'FLOW' : type.toUpperCase().slice(0, 5);
    const status = file.pending ? `${type} · Pending` : file.validation ? (file.validation.complete ? 'Validated workflow' : `Workflow ${file.validation.reviewed}/${file.validation.total}`) : type;
    return `<button class="file explorer-document" data-path="${esc(file.path)}"><span class="file-icon">${esc(icon)}</span><span><strong>${esc(file.title || file.name.replace(/\.[^.]+$/, ''))}</strong><small class="${file.validation && !file.validation.complete ? 'needs-validation' : ''}">${esc(status)}</small></span><b>›</b></button>`;
  }).join('');
  return folders + files;
}

function renderTopicExplorer(root) {
  if (state.domain !== 'steps' || state.selected) return;
  const treeRoot = root || buildFileTree();
  const draft = state.newWorkflowDraft?.topic === state.activeTopic ? state.newWorkflowDraft : null;
  const topic = state.activeTopic && (treeRoot.folders[state.activeTopic] || (draft ? {folders: {}, files: []} : null));
  $('#editor').hidden = true;
  $('#empty').hidden = false;
  $('#empty').className = 'empty topic-browser';
  if (!topic) {
    $('#empty').innerHTML = '<div class="topic-browser-empty"><div class="empty-icon">⌁</div><h2>Choose a main topic</h2><p>Select a folder on the left to browse its subtopics and workflows.</p></div>';
    return;
  }
  let browsePath = state.topicBrowsePath || '';
  let browseNode = topicNodeAt(topic, browsePath);
  if (!browseNode) { browsePath = ''; state.topicBrowsePath = ''; browseNode = topic; }
  rememberTopic('steps', state.activeTopic);
  const typeCounts = allTreeFiles(topic).reduce((counts, file) => { const type = documentTypeLabel(file); counts[type] = (counts[type] || 0) + 1; return counts; }, {});
  const topicSummary = Object.entries(typeCounts).map(([type, count]) => `${count} ${type.toLowerCase()}${count === 1 || type === 'FAQ' ? '' : 's'}`).join(' · ') || '0 items';
  const currentStage = state.topicLabels[state.activeTopic] || '';
  const locationOptions = draft ? [`<option value="" ${!draft.parent ? 'selected' : ''}>Main topic (${esc(draft.topic)})</option>`, ...treeFolderPaths(topic).map(path => `<option value="${esc(path)}" ${draft.parent === path ? 'selected' : ''}>${esc(path.split('/').map(part => `› ${part}`).join(' '))}</option>`)].join('') : '';
  const typeChoices = draft ? [{type:'workflow', icon:'1→', title:'Workflow', detail:'Steps and instructions'}, {type:'tips', icon:'✦', title:'Tips', detail:'Guidance and substeps'}, {type:'faq', icon:'?', title:'FAQ', detail:'Questions and answers'}, {type:'overview', icon:'◎', title:'Overview', detail:'Summary content'}, {type:'rules', icon:'✓', title:'Rules', detail:'Policies and conditions'}].map(choice => `<button type="button" class="content-type-card ${draft.type === choice.type ? 'selected' : ''}" data-content-type="${choice.type}" aria-pressed="${draft.type === choice.type}"><span>${choice.icon}</span><strong>${choice.title}</strong><small>${choice.detail}</small></button>`).join('') : '';
  const createPanel = draft ? `<form class="workflow-create-panel" id="workflowCreateForm"><div class="new-content-heading"><p class="eyebrow">NEW CONTENT</p><strong>What would you like to create?</strong></div><div class="content-type-grid">${typeChoices}</div><div class="new-content-fields"><label>Create inside<select id="newContentParent">${locationOptions}</select><span class="field-help">Choose the parent location; do not type the full path.</span></label><label>Name / subtopic<input id="newWorkflowTitle" value="${esc(draft.title)}" placeholder="For example: test" required autofocus></label></div><div class="workflow-create-actions"><button type="button" id="cancelWorkflowCreate" class="secondary">Cancel</button><button type="submit">Create ${esc(draft.type || 'workflow')}</button></div></form>` : '';
  const topicManagementActions = state.me.approver ? '<details class="retail-manage-menu topic-manage-menu"><summary>Manage <span>⌄</span></summary><div><button class="secondary" id="renameMainTopic">Rename main topic</button><button class="danger topic-delete" id="deleteMainTopic">Delete main topic</button></div></details>' : '';
  const breadcrumbParts = browsePath.split('/').filter(Boolean);
  const breadcrumb = `<nav class="topic-browser-breadcrumb" aria-label="Current location"><button data-topic-crumb="">${esc(state.activeTopic)}</button>${breadcrumbParts.map((part, index) => `<span>›</span><button data-topic-crumb="${esc(breadcrumbParts.slice(0, index + 1).join('/'))}">${esc(part)}</button>`).join('')}</nav>`;
  const backButton = browsePath ? '<button class="secondary topic-level-back" id="topicLevelBack">← Back</button>' : '';
  const levelContent = renderTopicLevel(browseNode, browsePath);
  $('#empty').innerHTML = `<section class="topic-explorer"><header class="topic-explorer-head"><div class="topic-explorer-main"><p class="eyebrow">STEPS & WORKFLOWS</p><h2>${esc(state.activeTopic)}</h2><p>${esc(topicSummary)} · choose an item to open it</p><div class="topic-workflow-actions"><button class="secondary topic-new-workflow" id="explorerNewWorkflow">+ New</button>${topicManagementActions}</div></div><div class="topic-explorer-actions"><label class="topic-login-label">Login label<select id="topicLoginStage"><option value="">Choose a label</option><option value="pre_login" ${currentStage === 'pre_login' ? 'selected' : ''}>Pre-login</option><option value="post_login" ${currentStage === 'post_login' ? 'selected' : ''}>Post-login</option></select><span>Applied to every item in this main topic.</span></label></div></header>${createPanel}<div class="topic-level-navigation">${backButton}${breadcrumb}</div><div class="topic-explorer-tree topic-level-list">${levelContent || '<p class="no-results">No content in this location yet.</p>'}</div></section>`;
  document.querySelectorAll('#empty [data-path]').forEach(button => button.onclick = () => openFile(button.dataset.path));
  document.querySelectorAll('[data-topic-drill]').forEach(button => button.onclick = () => { state.topicBrowsePath = button.dataset.topicDrill; renderTopicExplorer(); });
  document.querySelectorAll('[data-topic-crumb]').forEach(button => button.onclick = () => { state.topicBrowsePath = button.dataset.topicCrumb; renderTopicExplorer(); });
  if ($('#topicLevelBack')) $('#topicLevelBack').onclick = () => { state.topicBrowsePath = browsePath.split('/').slice(0, -1).join('/'); renderTopicExplorer(); };
  $('#explorerNewWorkflow').onclick = () => { state.newWorkflowDraft = {topic:state.activeTopic, parent:browsePath, title:'', type:'workflow'}; renderTopicExplorer(); };
  if ($('#renameMainTopic')) $('#renameMainTopic').onclick = async () => {
    const previous = state.activeTopic; const title = prompt('Main topic name', previous);
    if (title === null || !title.trim() || title.trim() === previous) return;
    try {
      $('#renameMainTopic').disabled = true;
      const result = await api('/api/data/steps/topic', {method:'PUT', body:JSON.stringify({topic:previous, title:title.trim()})});
      await selectDomain('steps'); state.activeTopic = result.title; state.topicBrowsePath = ''; state.topicLabels = result.topic_labels || state.topicLabels; rememberTopic('steps', state.activeTopic); forgetSelection('steps'); renderFiles(); renderTopicExplorer(); toast(result.message);
    } catch (error) { showNotice(error.message, 'error'); renderTopicExplorer(); }
  };
  if ($('#deleteMainTopic')) $('#deleteMainTopic').onclick = async () => {
    const topicName = state.activeTopic;
    if (!confirm(`Delete the main topic “${topicName}”? This removes all of its workflows, related files, pending edits, and its entries in the AI knowledge base.`)) return;
    if (!buildFileTree().folders[topicName]) {
      state.activeTopic = null; state.topicBrowsePath = ''; state.selected = null; state.newWorkflowDraft = null;
      forgetTopic('steps'); forgetSelection('steps'); renderFiles(); toast('New main topic cancelled'); return;
    }
    try {
      $('#deleteMainTopic').disabled = true;
      const result = await api(`/api/data/steps/topic?topic=${encodeURIComponent(topicName)}`, {method:'DELETE'});
      state.activeTopic = null; state.topicBrowsePath = ''; state.selected = null; state.newWorkflowDraft = null;
      state.topicLabels = result.topic_labels || {};
      forgetTopic('steps'); forgetSelection('steps');
      await selectDomain('steps'); toast(result.message);
    } catch (error) { showNotice(error.message, 'error'); renderTopicExplorer(); }
  };
  $('#topicLoginStage').onchange = async event => {
    try {
      const result = await api('/api/data/steps/topic-label', {method:'PUT', body:JSON.stringify({topic:state.activeTopic, login_stage:event.target.value})});
      state.topicLabels = result.topic_labels || {};
      const refreshed = await api('/api/data/steps/files');
      state.files = refreshed.files; state.topicLabels = refreshed.topic_labels || state.topicLabels;
      renderFiles(); toast('Login label saved for this topic');
    } catch (error) { toast(error.message); renderTopicExplorer(); }
  };
  if ($('#workflowCreateForm')) {
    document.querySelectorAll('[data-content-type]').forEach(button => button.onclick = () => { state.newWorkflowDraft.type = button.dataset.contentType; renderTopicExplorer(); });
    $('#newContentParent').onchange = event => { state.newWorkflowDraft.parent = event.target.value; };
    $('#newWorkflowTitle').oninput = event => { state.newWorkflowDraft.title = event.target.value; };
    $('#cancelWorkflowCreate').onclick = () => { const isNewTopic = !buildFileTree().folders[state.activeTopic]; state.newWorkflowDraft = null; if (isNewTopic) state.activeTopic = null; renderFiles(); };
    $('#workflowCreateForm').onsubmit = event => { event.preventDefault(); createWorkflowFromDraft(); };
  }
}

function startNewWorkflow() {
  if (state.domain !== 'steps') return;
  $('#newMainTopicForm').hidden = false; $('#newWorkflow').hidden = true;
  $('#files').scrollTop = $('#files').scrollHeight;
  $('#newMainTopicName').focus();
}

async function createWorkflowFromDraft() {
  const draft = state.newWorkflowDraft;
  const clean = value => String(value || '').replace(/[<>:"/\\|?*]+/g, ' ').trim();
  const topic = clean(draft?.topic), parent = String(draft?.parent || '').split('/').map(clean).filter(part => part && part !== '.' && part !== '..').join('/'), title = clean(draft?.title), type = ['workflow','tips','faq','overview','rules'].includes(draft?.type) ? draft.type : 'workflow';
  if (!topic || !title) { toast('Add a name or subtopic'); return; }
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'overview';
  const filename = type === 'workflow' ? 'workflow.json' : type === 'overview' ? `${slug}_overview.json` : `${type}.json`;
  const base = parent ? `${topic}/${parent}` : topic;
  const path = type === 'overview' ? `${base}/${filename}` : `${base}/${title}/${filename}`;
  if (state.files.some(file => file.path.toLowerCase() === path.toLowerCase())) { toast(`${type === 'faq' ? 'FAQ' : type} already exists in this location`); return; }
  try {
    const templates = {
      workflow:{topic, title, login_stage:topicStageForPath(path), steps:[]},
      tips:{tips:[{title:'Add the first tip', text:'', items:[], substeps:[]}]},
      faq:[{question:'Add the first question', answer:'Add the approved answer'}],
      overview:{topic, title, description:'', features:[{name:'Add the first feature', details:['Describe what the customer can do']}]},
      rules:{Rules:['1. Add the first rule']}
    };
    const data = templates[type];
    await api(`/api/data/steps/file?path=${encodeURIComponent(path)}`, {method:'PUT', body:JSON.stringify({data})});
    state.newWorkflowDraft = null;
    await selectDomain('steps'); state.activeTopic = topic;
    await openFile(path); toast(`${type === 'faq' ? 'FAQ' : type[0].toUpperCase() + type.slice(1)} created. Complete its details, then submit for approval.`);
  } catch (error) { toast(error.message); }
}

function buildFileTree() {
  const query = $('#search').value.trim().toLowerCase();
  const root = {folders: {}, files: []};
  state.files.filter(file => !query || file.path.toLowerCase().includes(query) || String(file.title || '').toLowerCase().includes(query)).forEach(file => {
    const parts = file.path.split('/'); let node = root;
    for (const folder of parts.slice(0, -1)) node = node.folders[folder] ||= {folders: {}, files: []};
    node.files.push(file);
  });
  return root;
}

function topicStageForPath(path) { return state.topicLabels[(path || '').split('/')[0]] || ''; }

function countTreeFiles(node) { return node.files.length + Object.values(node.folders).reduce((total, child) => total + countTreeFiles(child), 0); }
function allTreeFiles(node) { return [...node.files, ...Object.values(node.folders).flatMap(allTreeFiles)]; }
function treeFolderPaths(node, prefix = '') { return Object.entries(node.folders).flatMap(([name, child]) => { const path = prefix ? `${prefix}/${name}` : name; return [path, ...treeFolderPaths(child, path)]; }); }

async function openFile(path) {
  const domain = state.domain; const requestId = ++state.requestId;
  setDirty(false);
  if (domain === 'steps' && !state.activeTopic) { state.activeTopic = path.split('/')[0] || null; if (state.activeTopic) rememberTopic('steps', state.activeTopic); }
  if (domain === 'retail') {
    const parts = path.split('/'); const rootName = (state.files.find(file => file.path === path)?.title || parts[0]).replace(/\.[^.]+$/, '').trim();
    const belongsToCollection = parts.length > 1 || state.files.some(file => file.path.startsWith(`${rootName}/`));
    state.retailCollection = belongsToCollection ? (parts.length > 1 ? parts[0] : rootName) : null;
    rememberRetailCollection(state.retailCollection);
  }
  state.selected = state.files.find(file => file.path === path);
  if (!state.selected) return;
  rememberSelection(domain, path); renderFiles(); $('#empty').hidden = true; $('#editor').hidden = false;
  $('#visual').innerHTML = '<div class="source-preview"><h3>Loading data…</h3></div>'; $('#raw').hidden = true;
  $('#breadcrumb').textContent = `${state.domain} / ${path.split('/').slice(0, -1).join(' / ')}`; $('#docTitle').textContent = state.selected.title || state.selected.name;
  const validationText = state.selected.validation ? (state.selected.validation.complete ? 'Validated' : `Validation incomplete · ${state.selected.validation.reviewed}/${state.selected.validation.total} fields reviewed`) : 'Validated';
  $('#docMeta').textContent = state.selected.pending ? `Pending approval · submitted by ${state.selected.pending.submitted_by}` : `${validationText} · updated ${new Date(state.selected.updatedAt).toLocaleString()}`;
  $('#notice').hidden = true;
  const workflowEditable = state.domain === 'steps' && path.endsWith('.json');
  const retailEditable = state.domain === 'retail' && (path.endsWith('.pdf') || path.endsWith('.product'));
  const editable = workflowEditable || retailEditable;
  $('#approve').hidden = !(state.me.approver && editable);
  $('#save').hidden = !editable;
  $('#save').textContent = editable && state.me.approver ? 'Submit and approve' : 'Submit changes';
  $('#save').classList.toggle('submit-approve', editable && state.me.approver);
  $('#backToTopic').hidden = state.domain !== 'steps' || !state.activeTopic;
  $('#deleteWorkflow').hidden = !(workflowEditable && state.me.approver);
  $('#deleteWorkflow').textContent = 'Delete workflow';
  $('#deleteRetailPdf').hidden = !(retailEditable && state.me.approver && !state.selected.new);
  $('#deleteRetailPdf').textContent = 'Delete topic';
  const inRetailCollection = retailEditable && !!state.retailCollection;
  $('#newRetailSubproduct').hidden = !retailEditable || inRetailCollection;
  $('#backToRetailCollection').hidden = !inRetailCollection;
  $('#exportRetailProduct').hidden = !retailEditable;
  try {
    if (workflowEditable) {
      const result = await api(`/api/data/steps/file?path=${encodeURIComponent(path)}`); if (requestId !== state.requestId || domain !== state.domain) return;
      state.selected.data = result.data; state.selected.pending = result.pending || state.selected.pending || null; state.raw = false; state.workflowMode = state.selected.pending ? 'review' : 'edit'; state.workflowWizard = null; state.workflowWizardDismissed = false; updateReferenceMeta(); $('#deleteWorkflow').textContent = `Delete ${referenceDocumentLabel(result.data)}`; renderEditor();
    } else if (retailEditable) {
      const result = await api(`/api/data/retail/extracted?path=${encodeURIComponent(path)}`); if (requestId !== state.requestId || domain !== state.domain) return;
      state.selected.data = result.data; state.selected.pending = result.pending || state.selected.pending || null; state.selected.changes = result.changes || {document:[], sections:[]}; state.raw = false;
      state.retailMode = result.pending ? 'review' : 'edit'; state.retailRenaming = false; state.retailAddingContent = false; state.retailAddingSection = false; state.retailAddingSubsection = false; state.retailGroupPath = null; state.retailRenamingCategory = null; state.retailWizard = null; state.retailWizardDismissed = false;
      const firstChangedSection = state.selected.changes.sections.find(change => Number.isInteger(change.index));
      state.retailSection = firstChangedSection?.index ?? 0; state.retailCategory = firstChangedSection ? retailCategoryFor(result.data.sections[firstChangedSection.index], result.data, firstChangedSection.index) : null; renderRetailEditor();
    } else {
      $('#visual').innerHTML = `<div class="source-preview"><div>${path.endsWith('.pdf') ? 'PDF' : 'XLS'}</div><h3>${esc(state.selected.name)}</h3><p>This is the currently validated source.</p><a href="/api/data/${state.domain}/file?path=${encodeURIComponent(path)}" target="_blank">Open validated source ↗</a></div>`;
    }
  } catch (error) {
    if (requestId !== state.requestId) return;
    $('#visual').innerHTML = '';
    showNotice(`Could not load this source: ${error.message}. Restart the latest application server and refresh the page.`, 'error');
  }
}

function imageUrl(name) { const directory = state.selected.path.split('/').slice(0, -1).join('/'); return `/api/data/steps/asset?path=${encodeURIComponent(directory + '/' + name)}`; }
function reviewedFields(data) {
  data.validation = data.validation && typeof data.validation === 'object' ? data.validation : {};
  data.validation.reviewed_fields = Array.isArray(data.validation.reviewed_fields) ? data.validation.reviewed_fields : [];
  return data.validation.reviewed_fields;
}
function invalidateReview(data, field) {
  data.validation.reviewed_fields = reviewedFields(data).filter(value => value !== field);
  delete data.validation.complete; delete data.validation.validated_by; delete data.validation.validated_at;
  setDirty(true);
}
function invalidateStepReviews(data) {
  data.validation.reviewed_fields = reviewedFields(data).filter(field => !field.startsWith('steps.'));
  delete data.validation.complete; delete data.validation.validated_by; delete data.validation.validated_at;
  setDirty(true);
}
function workflowReviewStatus(data) {
  const required = ['topic', 'title'];
  const addStepFields = (steps, prefix) => (Array.isArray(steps) ? steps : []).forEach((step, index) => { required.push(`${prefix}.${index}.label`, `${prefix}.${index}.text`, `${prefix}.${index}.image`); addStepFields(step?.substeps, `${prefix}.${index}.substeps`); });
  addStepFields(data.steps, 'steps');
  const reviewed = new Set(reviewedFields(data));
  const complete = required.every(field => reviewed.has(field));
  return {required, reviewed: required.filter(field => reviewed.has(field)).length, complete};
}
function workflowSubsteps(step, stepIndex) {
  const substeps = Array.isArray(step.substeps) ? step.substeps : [];
  if (!substeps.length) return '';
  if (state.workflowMode === 'review') return `<section class="workflow-substeps"><div class="workflow-substeps-head"><strong>${substeps.length} substeps</strong></div>${substeps.map((substep, substepIndex) => { const images = String(substep.image || '').split(',').map(value => value.trim()).filter(Boolean); return `<article class="workflow-substep review-substep"><div><span class="entry-kind">SUBSTEP</span><strong>${esc(substep.step ?? `${step.step}.${substepIndex + 1}`)}</strong></div><div class="review-content-heading"><span>Instruction</span></div><p>${esc(substep.text || 'No content entered')}</p>${images.length ? `<div class="shots">${images.map(image => `<div class="shot"><a href="${imageUrl(image)}" target="_blank"><img src="${imageUrl(image)}" alt="${esc(image)}" loading="lazy"></a></div>`).join('')}</div>` : ''}</article>`; }).join('')}</section>`;
  return `<section class="workflow-substeps"><div class="workflow-substeps-head"><strong>${substeps.length} substeps</strong><button type="button" class="secondary" data-substep-add="${stepIndex}">+ Add substep</button></div>${substeps.map((substep, substepIndex) => `<article class="workflow-substep"><div class="workflow-substep-top"><span class="entry-kind">SUBSTEP</span><label>Substep label<input data-substep-label="${stepIndex}:${substepIndex}" value="${esc(substep.step ?? '')}"></label><button type="button" class="icon-btn" data-substep-remove="${stepIndex}:${substepIndex}" title="Delete substep">×</button></div><textarea data-substep-text="${stepIndex}:${substepIndex}" placeholder="Describe this substep clearly…">${esc(substep.text || '')}</textarea></article>`).join('')}</section>`;
}
function entry(step, index) {
  const images = String(step.image || '').split(',').map(value => value.trim()).filter(Boolean); const type = 'Note' in step ? 'Note' : step.variant === 'tip' ? 'Tip' : 'Step'; const label = step.Note ?? step.step ?? index + 1;
  const gallery = images.map((image, imageIndex) => `<div class="shot"><a href="${imageUrl(image)}" target="_blank" title="Open full size"><img src="${imageUrl(image)}" alt="${esc(image)}" loading="lazy"></a>${state.workflowMode === 'edit' ? `<button type="button" data-image-remove="${index}" data-image-index="${imageIndex}" title="Remove image">×</button>` : ''}</div>`).join('');
  const data = state.selected.data;
  if (state.workflowMode === 'review') return `<article class="entry review-entry"><header class="review-entry-head"><div class="review-step-title"><span class="review-step-number">${index + 1}</span><div><span class="entry-kind ${type.toLowerCase()}">${type}</span><strong>${esc(type)} ${esc(label)}</strong></div></div></header><section class="review-content"><div class="review-instruction"><span>Instruction</span><p>${esc(step.text || 'No content entered')}</p></div></section>${gallery ? `<section class="review-media"><div class="review-content-heading"><span>Related image${images.length === 1 ? '' : 's'}</span></div><div class="shots review-shots">${gallery}</div></section>` : ''}${workflowSubsteps(step, index)}</article>`;
  return `<article class="entry"><div class="entry-top"><span class="entry-kind ${type.toLowerCase()}">${type}</span><label>${type} label<input data-label="${index}" value="${esc(label)}"></label><button data-remove="${index}" class="icon-btn" title="Delete entry">×</button></div><textarea data-text="${index}" placeholder="Describe this ${type.toLowerCase()} clearly…">${esc(step.text)}</textarea><div class="image-tools"><label class="image-add">+ Add image<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" data-image-add="${index}" hidden></label><span>Add, replace, or remove images as needed.</span></div>${gallery ? `<div class="shots">${gallery}</div>` : ''}${workflowSubsteps(step, index)}</article>`;
}

function contentModeHeader(reviewing, {reviewTitle, reviewBody, editLead}) {
  const modeSwitch = reviewing ? `<div class="workflow-mode-switch" role="group" aria-label="Content task"><button class="secondary" data-workflow-mode="edit">← Back to editing</button></div>` : '';
  const editTail = state.me.approver ? 'Select Submit and approve when ready.' : 'Submit for approval when ready.';
  const summary = reviewing
    ? `<div class="validation-summary"><div><strong>${reviewTitle}</strong><span>${reviewBody}</span></div></div>`
    : `<div class="edit-summary"><strong>Edit content</strong><span>${editLead} ${editTail}</span></div>`;
  return modeSwitch + summary;
}

function renderFaqEditor(items) {
  const reviewing = state.workflowMode === 'review';
  const cards = items.map((item, index) => reviewing ? `<article class="reference-entry review-entry"><header><span class="entry-kind">FAQ ${index + 1}</span></header><div class="reference-review-block"><span>Question</span><p>${esc(item.question || 'No question entered')}</p></div><div class="reference-review-block"><span>Answer</span><p>${esc(item.answer || 'No answer entered')}</p></div></article>` : `<article class="reference-entry"><header><span class="entry-kind">FAQ ${index + 1}</span><button class="icon-btn" type="button" data-faq-remove="${index}" title="Delete FAQ">×</button></header><label>Question<textarea data-faq-question="${index}" placeholder="Enter the customer question">${esc(item.question || '')}</textarea></label><label>Answer<textarea data-faq-answer="${index}" placeholder="Enter the approved answer">${esc(item.answer || '')}</textarea></label></article>`).join('');
  const header = contentModeHeader(reviewing, {reviewTitle: 'Review FAQs', reviewBody: 'Review all questions and answers, then select Approve all.', editLead: 'Update the questions and answers.'});
  $('#visual').innerHTML = `<div class="reference-editor">${header}<div class="section-title"><div><p class="eyebrow">FREQUENTLY ASKED QUESTIONS</p><h3>${items.length} questions</h3></div>${reviewing ? '' : '<button id="addFaq" class="secondary">+ Add question</button>'}</div><div class="reference-list">${cards || '<p class="no-results">No questions yet. Add the first FAQ.</p>'}</div></div>`;
  $('#approve').hidden = !(state.me.approver && reviewing); $('#save').hidden = reviewing;
  document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); });
  if (reviewing) return;
  $('#addFaq').onclick = () => { items.push({question:'', answer:''}); setDirty(true); renderFaqEditor(items); };
  document.querySelectorAll('[data-faq-question]').forEach(input => input.oninput = () => { items[+input.dataset.faqQuestion].question = input.value; setDirty(true); });
  document.querySelectorAll('[data-faq-answer]').forEach(input => input.oninput = () => { items[+input.dataset.faqAnswer].answer = input.value; setDirty(true); });
  document.querySelectorAll('[data-faq-remove]').forEach(button => button.onclick = () => { items.splice(+button.dataset.faqRemove, 1); setDirty(true); renderFaqEditor(items); });
}

function tipGroups(tips) {
  const groups = [];
  let current = null;
  tips.forEach((tip, index) => {
    const text = String(tip || '');
    if (!current || /^\s*\d+\s*-/.test(text)) { current = {index, text: tip, children: [], itemCount: 1, lastIndex: index, activeChild: null}; groups.push(current); return; }
    current.itemCount += 1; current.lastIndex = index;
    const isRoman = /^\s*(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\./i.test(text);
    const isLetter = /^\s*[a-hj-uw-z]\./i.test(text);
    if (isLetter) { const child = {index, text: tip, children: [], lastIndex: index}; current.children.push(child); current.activeChild = child; }
    else if (isRoman && current.activeChild) { current.activeChild.children.push({index, text: tip}); current.activeChild.lastIndex = index; }
    else current.children.push({index, text: tip, children: []});
  });
  return groups;
}

function romanNumeral(value) { const numerals = [[10, 'x'], [9, 'ix'], [5, 'v'], [4, 'iv'], [1, 'i']]; let result = ''; for (const [number, symbol] of numerals) while (value >= number) { result += symbol; value -= number; } return result; }
function letterLabel(value) { return String.fromCharCode(96 + Math.max(1, value)); }

function renderTipsEditor(data) {
  const tips = data.tips;
  if (state.selected?.new && tips.length && tips.every(item => typeof item === 'string')) { data.tips = tips.map(item => ({title:String(item).replace(/^\s*\d+\s*-\s*/, ''), text:'', items:[], substeps:[]})); setDirty(true); renderStructuredTipsEditor(data); return; }
  if (tips.some(item => item && typeof item === 'object')) { renderStructuredTipsEditor(data); return; }
  const groups = tipGroups(tips);
  const reviewing = state.workflowMode === 'review';
  const cards = groups.map((group, groupIndex) => reviewing ? `<article class="reference-entry tip-entry review-entry"><header><span class="entry-kind">TIP ${groupIndex + 1}</span></header><p class="tip-review-text">${esc(group.text || 'No tip entered')}</p>${group.children.length ? `<div class="tip-substeps review-tip-substeps">${group.children.map(child => `<div><span>↳</span><p>${esc(child.text || '')}</p>${child.children.map(grandchild => `<div class="tip-subsubstep"><span>↳</span><p>${esc(grandchild.text || '')}</p></div>`).join('')}</div>`).join('')}</div>` : ''}</article>` : `<article class="reference-entry tip-entry"><header><span class="entry-kind">TIP ${groupIndex + 1}</span><button class="icon-btn" type="button" data-tip-remove-group="${group.index}" data-tip-group-size="${group.itemCount}" title="Delete tip and its substeps">×</button></header><textarea aria-label="Tip ${groupIndex + 1}" data-tip-text="${group.index}" placeholder="Enter a tip">${esc(group.text || '')}</textarea>${group.children.length ? `<div class="tip-substeps">${group.children.map(child => `<div class="tip-substep"><span>↳</span><textarea aria-label="Substep" data-tip-text="${child.index}" placeholder="Enter a substep">${esc(child.text || '')}</textarea><button class="icon-btn" type="button" data-tip-remove="${child.index}" title="Delete substep">×</button></div>${child.children.length ? `<div class="tip-subsubsteps">${child.children.map(grandchild => `<div class="tip-substep tip-subsubstep"><span>↳</span><textarea aria-label="Sub-substep" data-tip-text="${grandchild.index}" placeholder="Enter a sub-substep">${esc(grandchild.text || '')}</textarea><button class="icon-btn" type="button" data-tip-remove="${grandchild.index}" title="Delete sub-substep">×</button></div>`).join('')}</div>` : ''}<button class="secondary tip-add-subsubstep" type="button" data-tip-add-subsubstep="${child.lastIndex}" data-tip-next-roman="${child.children.length + 1}">+ Add sub-substep</button>`).join('')}</div>` : ''}<button class="secondary tip-add-substep" type="button" data-tip-last-index="${group.lastIndex}" data-tip-next-letter="${group.children.length + 1}">+ Add substep</button></article>`).join('');
  const header = contentModeHeader(reviewing, {reviewTitle: 'Review tips', reviewBody: 'Review all tips and substeps, then select Approve all.', editLead: 'Update the tips and substeps.'});
  $('#visual').innerHTML = `<div class="reference-editor">${header}<div class="section-title"><div><p class="eyebrow">TIPS</p><h3>${groups.length} main tips</h3></div>${reviewing ? '' : '<button id="addTip" class="secondary">+ Add tip</button>'}</div><div class="reference-list">${cards || '<p class="no-results">No tips yet. Add the first tip.</p>'}</div></div>`;
  $('#approve').hidden = !(state.me.approver && reviewing); $('#save').hidden = reviewing;
  document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); });
  if (reviewing) return;
  $('#addTip').onclick = () => { tips.push(`${groups.length + 1}- New tip`); setDirty(true); renderTipsEditor(data); };
  document.querySelectorAll('[data-tip-text]').forEach(input => input.oninput = () => { tips[+input.dataset.tipText] = input.value; setDirty(true); });
  document.querySelectorAll('[data-tip-remove]').forEach(button => button.onclick = () => { tips.splice(+button.dataset.tipRemove, 1); setDirty(true); renderTipsEditor(data); });
  document.querySelectorAll('[data-tip-remove-group]').forEach(button => button.onclick = () => { tips.splice(+button.dataset.tipRemoveGroup, +button.dataset.tipGroupSize); setDirty(true); renderTipsEditor(data); });
  document.querySelectorAll('[data-tip-add-substep]').forEach(button => button.onclick = () => { tips.splice(+button.dataset.tipLastIndex + 1, 0, `${letterLabel(+button.dataset.tipNextLetter)}. New substep`); setDirty(true); renderTipsEditor(data); });
  document.querySelectorAll('[data-tip-add-subsubstep]').forEach(button => button.onclick = () => { tips.splice(+button.dataset.tipAddSubsubstep + 1, 0, `${romanNumeral(+button.dataset.tipNextRoman)}. New sub-substep`); setDirty(true); renderTipsEditor(data); });
}

function renderStructuredTipsEditor(data) {
  const tips = data.tips;
  const reviewing = state.workflowMode === 'review';
  const cards = tips.map((tip, tipIndex) => {
    const itemList = Array.isArray(tip.items) ? tip.items : [];
    const substeps = Array.isArray(tip.substeps) ? tip.substeps : [];
    if (reviewing) return `<article class="reference-entry review-entry"><header><span class="entry-kind">TIP ${tipIndex + 1}</span><strong>${esc(tip.title || 'Untitled tip')}</strong></header>${tip.text ? `<div class="reference-review-block"><span>Details</span><p>${esc(tip.text)}</p></div>` : ''}<div class="reference-review-block"><span>Items</span>${itemList.length ? `<ul class="structured-tip-list">${itemList.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : '<p>No items</p>'}</div><div class="reference-review-block"><span>Substeps</span>${substeps.length ? `<ol class="structured-tip-list">${substeps.map(step => `<li>${esc(step)}</li>`).join('')}</ol>` : '<p>No substeps</p>'}</div></article>`;
    return `<article class="reference-entry structured-tip-entry"><header><span class="entry-kind">TIP ${tipIndex + 1}</span><button class="icon-btn" type="button" data-structured-tip-remove="${tipIndex}" title="Delete tip">×</button></header><label>Title<input data-structured-tip-title="${tipIndex}" value="${esc(tip.title || '')}" placeholder="Enter the tip title"></label><label>Details<textarea data-structured-tip-text="${tipIndex}" placeholder="Enter supporting details">${esc(tip.text || '')}</textarea></label><div class="structured-tip-items"><strong>Items</strong>${itemList.map((item, itemIndex) => `<div><input data-structured-tip-item="${tipIndex}:${itemIndex}" value="${esc(item)}"><button class="icon-btn" type="button" data-structured-tip-item-remove="${tipIndex}:${itemIndex}" title="Delete item">×</button></div>`).join('')}<button class="secondary" type="button" data-structured-tip-item-add="${tipIndex}">+ Add item</button></div><div class="structured-tip-items"><strong>Substeps</strong>${substeps.map((step, stepIndex) => `<div><input data-structured-tip-substep="${tipIndex}:${stepIndex}" value="${esc(step)}"><button class="icon-btn" type="button" data-structured-tip-substep-remove="${tipIndex}:${stepIndex}" title="Delete substep">×</button></div>`).join('')}<button class="secondary" type="button" data-structured-tip-substep-add="${tipIndex}">+ Add substep</button></div></article>`;
  }).join('');
  const header = contentModeHeader(reviewing, {reviewTitle: 'Review tips', reviewBody: 'Review all structured tips, then select Approve all.', editLead: 'Update titles, details, and item lists.'});
  $('#visual').innerHTML = `<div class="reference-editor">${header}<div class="section-title"><div><p class="eyebrow">TIPS</p><h3>${tips.length} main tips</h3></div>${reviewing ? '' : '<button id="addStructuredTip" class="secondary">+ Add tip</button>'}</div><div class="reference-list">${cards || '<p class="no-results">No tips yet. Add the first tip.</p>'}</div></div>`;
  $('#approve').hidden = !(state.me.approver && reviewing); $('#save').hidden = reviewing;
  document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); });
  if (reviewing) return;
  $('#addStructuredTip').onclick = () => { tips.push({title:'New tip', text:'', items:[], substeps:[]}); setDirty(true); renderStructuredTipsEditor(data); };
  document.querySelectorAll('[data-structured-tip-title]').forEach(input => input.oninput = () => { tips[+input.dataset.structuredTipTitle].title = input.value; setDirty(true); });
  document.querySelectorAll('[data-structured-tip-text]').forEach(input => input.oninput = () => { tips[+input.dataset.structuredTipText].text = input.value; setDirty(true); });
  document.querySelectorAll('[data-structured-tip-item]').forEach(input => input.oninput = () => { const [tipIndex,itemIndex] = input.dataset.structuredTipItem.split(':').map(Number); tips[tipIndex].items[itemIndex] = input.value; setDirty(true); });
  document.querySelectorAll('[data-structured-tip-item-add]').forEach(button => button.onclick = () => { const tip = tips[+button.dataset.structuredTipItemAdd]; (tip.items ||= []).push('New item'); setDirty(true); renderStructuredTipsEditor(data); });
  document.querySelectorAll('[data-structured-tip-item-remove]').forEach(button => button.onclick = () => { const [tipIndex,itemIndex] = button.dataset.structuredTipItemRemove.split(':').map(Number); tips[tipIndex].items.splice(itemIndex,1); setDirty(true); renderStructuredTipsEditor(data); });
  document.querySelectorAll('[data-structured-tip-substep]').forEach(input => input.oninput = () => { const [tipIndex,stepIndex] = input.dataset.structuredTipSubstep.split(':').map(Number); tips[tipIndex].substeps[stepIndex] = input.value; setDirty(true); });
  document.querySelectorAll('[data-structured-tip-substep-add]').forEach(button => button.onclick = () => { const tip = tips[+button.dataset.structuredTipSubstepAdd]; (tip.substeps ||= []).push('New substep'); setDirty(true); renderStructuredTipsEditor(data); });
  document.querySelectorAll('[data-structured-tip-substep-remove]').forEach(button => button.onclick = () => { const [tipIndex,stepIndex] = button.dataset.structuredTipSubstepRemove.split(':').map(Number); tips[tipIndex].substeps.splice(stepIndex,1); setDirty(true); renderStructuredTipsEditor(data); });
  document.querySelectorAll('[data-structured-tip-remove]').forEach(button => button.onclick = () => { tips.splice(+button.dataset.structuredTipRemove,1); setDirty(true); renderStructuredTipsEditor(data); });
}

function renderRulesEditor(data) {
  const rules = data.Rules;
  const reviewing = state.workflowMode === 'review';
  const cards = rules.map((rule, index) => reviewing ? `<article class="reference-entry review-entry"><header><span class="entry-kind">RULE ${index + 1}</span></header><p class="tip-review-text">${esc(rule || 'No rule entered')}</p></article>` : `<article class="reference-entry"><header><span class="entry-kind">RULE ${index + 1}</span><button class="icon-btn" type="button" data-rule-remove="${index}" title="Delete rule">×</button></header><textarea aria-label="Rule ${index + 1}" data-rule-text="${index}" placeholder="Enter a rule">${esc(rule || '')}</textarea></article>`).join('');
  const header = contentModeHeader(reviewing, {reviewTitle: 'Review rules', reviewBody: 'Review all rules, then select Approve all.', editLead: 'Update the rules.'});
  $('#visual').innerHTML = `<div class="reference-editor">${header}<div class="section-title"><div><p class="eyebrow">RULES</p><h3>${rules.length} rules</h3></div>${reviewing ? '' : '<button id="addRule" class="secondary">+ Add rule</button>'}</div><div class="reference-list">${cards || '<p class="no-results">No rules yet. Add the first rule.</p>'}</div></div>`;
  $('#approve').hidden = !(state.me.approver && reviewing); $('#save').hidden = reviewing;
  document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); });
  if (reviewing) return;
  $('#addRule').onclick = () => { rules.push(`${rules.length + 1}. New rule`); setDirty(true); renderRulesEditor(data); };
  document.querySelectorAll('[data-rule-text]').forEach(input => input.oninput = () => { rules[+input.dataset.ruleText] = input.value; setDirty(true); });
  document.querySelectorAll('[data-rule-remove]').forEach(button => button.onclick = () => { rules.splice(+button.dataset.ruleRemove, 1); setDirty(true); renderRulesEditor(data); });
}

function renderOverviewEditor(data) {
  const sections = data.overview;
  const reviewing = state.workflowMode === 'review';
  const cards = sections.map((section, index) => reviewing ? `<article class="reference-entry review-entry"><header><span class="entry-kind">OVERVIEW ${index + 1}</span></header><p class="tip-review-text">${esc(section || 'No overview text entered')}</p></article>` : `<article class="reference-entry"><header><span class="entry-kind">OVERVIEW ${index + 1}</span><button class="icon-btn" type="button" data-overview-remove="${index}" title="Delete overview section">×</button></header><textarea aria-label="Overview section ${index + 1}" data-overview-text="${index}" placeholder="Enter overview text">${esc(section || '')}</textarea></article>`).join('');
  const header = contentModeHeader(reviewing, {reviewTitle: 'Review overview', reviewBody: 'Review the overview content, then select Approve all.', editLead: 'Update the overview.'});
  $('#visual').innerHTML = `<div class="reference-editor">${header}<div class="section-title"><div><p class="eyebrow">OVERVIEW</p><h3>${sections.length} section${sections.length === 1 ? '' : 's'}</h3></div>${reviewing ? '' : '<button id="addOverview" class="secondary">+ Add section</button>'}</div><div class="reference-list">${cards || '<p class="no-results">No overview content yet. Add the first section.</p>'}</div></div>`;
  $('#approve').hidden = !(state.me.approver && reviewing); $('#save').hidden = reviewing;
  document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); });
  if (reviewing) return;
  $('#addOverview').onclick = () => { sections.push('New overview section'); setDirty(true); renderOverviewEditor(data); };
  document.querySelectorAll('[data-overview-text]').forEach(input => input.oninput = () => { sections[+input.dataset.overviewText] = input.value; setDirty(true); });
  document.querySelectorAll('[data-overview-remove]').forEach(button => button.onclick = () => { sections.splice(+button.dataset.overviewRemove, 1); setDirty(true); renderOverviewEditor(data); });
}

function renderFeaturesEditor(data) {
  const features = data.features;
  if (state.workflowMode === 'review') {
    const cards = features.map((feature, index) => `<article class="reference-entry review-entry"><header><span class="entry-kind">FEATURE ${index + 1}</span><strong>${esc(feature.name || 'Unnamed feature')}</strong></header><div class="reference-review-block"><span>What the customer can do</span>${(Array.isArray(feature.details) ? feature.details : []).map(renderFeatureDetailReview).join('') || '<p>No details entered</p>'}</div></article>`).join('');
    $('#visual').innerHTML = `<div class="features-editor">${contentModeHeader(true, {reviewTitle: 'Review features', reviewBody: 'Review all features, then select Approve all.'})}<div class="section-title"><div><p class="eyebrow">FEATURES</p><h3>${features.length} features</h3></div></div><div class="reference-list">${cards}</div></div>`;
    $('#approve').hidden = !state.me.approver; $('#save').hidden = true;
    document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); }); return;
  }
  const cards = features.map((feature, featureIndex) => { const structured = (Array.isArray(feature.details) ? feature.details : []).some(detail => detail && typeof detail === 'object' && !Array.isArray(detail)); return `<article class="feature-entry"><header><label>Feature name<input data-feature-name="${featureIndex}" value="${esc(feature.name || '')}" placeholder="Enter feature name"></label><button class="icon-btn" type="button" data-feature-remove="${featureIndex}" title="Delete feature">×</button></header><div class="feature-details"><strong>What the customer can do</strong>${(Array.isArray(feature.details) ? feature.details : []).map((detail, detailIndex) => renderFeatureDetailEditor(detail, featureIndex, detailIndex)).join('')}<button class="secondary feature-add-detail" type="button" data-feature-detail-add="${featureIndex}">${structured ? '+ Add structured record' : '+ Add detail'}</button></div></article>`; }).join('');
  $('#visual').innerHTML = `<div class="features-editor">${contentModeHeader(false, {editLead: 'Update the features.'})}<div class="form-card overview-description"><label>Description<textarea id="overviewDescription" placeholder="Describe this overview">${esc(data.description || '')}</textarea></label></div><div class="section-title"><div><p class="eyebrow">FEATURES</p><h3>${features.length} features</h3></div><button id="addFeature" class="secondary">+ Add feature</button></div><div class="feature-list">${cards || '<p class="no-results">No features yet. Add the first feature.</p>'}</div></div>`;
  $('#approve').hidden = true; $('#save').hidden = false;
  document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); });
  $('#overviewDescription').oninput = event => { data.description = event.target.value; setDirty(true); };
  $('#addFeature').onclick = () => { features.push({name:'', details:['']}); setDirty(true); renderFeaturesEditor(data); };
  document.querySelectorAll('[data-feature-name]').forEach(input => input.oninput = () => { data.features[+input.dataset.featureName].name = input.value; setDirty(true); });
  document.querySelectorAll('[data-feature-detail]').forEach(input => input.oninput = () => { const [featureIndex, detailIndex] = input.dataset.featureDetail.split(':').map(Number); data.features[featureIndex].details[detailIndex] = input.value; setDirty(true); });
  document.querySelectorAll('[data-feature-object-value]').forEach(input => input.onchange = () => { const [featureIndex, detailIndex, keyIndex] = input.dataset.featureObjectValue.split(':').map(Number); const detail = data.features[featureIndex].details[detailIndex]; const key = Object.keys(detail)[keyIndex]; const current = detail[key]; try { detail[key] = parseStructuredValue(input.value, current); setDirty(true); } catch { toast(`Enter valid JSON for ${key}`); input.value = structuredValueText(current); } });
  document.querySelectorAll('[data-feature-remove]').forEach(button => button.onclick = () => { data.features.splice(+button.dataset.featureRemove, 1); setDirty(true); renderFeaturesEditor(data); });
  document.querySelectorAll('[data-feature-detail-remove]').forEach(button => button.onclick = () => { const [featureIndex, detailIndex] = button.dataset.featureDetailRemove.split(':').map(Number); data.features[featureIndex].details.splice(detailIndex, 1); setDirty(true); renderFeaturesEditor(data); });
  document.querySelectorAll('[data-feature-detail-add]').forEach(button => button.onclick = () => { const feature = data.features[+button.dataset.featureDetailAdd]; const schema = [...feature.details].reverse().find(detail => detail && typeof detail === 'object' && !Array.isArray(detail)); feature.details.push(schema ? Object.fromEntries(Object.entries(schema).map(([key,value]) => [key, emptyStructuredValue(value)])) : ''); setDirty(true); renderFeaturesEditor(data); });
}

function structuredValueText(value) { return value === null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
function emptyStructuredValue(value) { if (value === null) return null; if (Array.isArray(value)) return []; if (typeof value === 'object') return {}; if (typeof value === 'number') return 0; if (typeof value === 'boolean') return false; return ''; }
function parseStructuredValue(value, current) { if (current === null) return value.trim() ? value : null; if (typeof current === 'number') { const number = Number(value); if (!Number.isFinite(number)) throw new Error('number'); return number; } if (typeof current === 'boolean') return value.toLowerCase() === 'true'; if (typeof current === 'object') return JSON.parse(value); return value; }
function renderFeatureDetailEditor(detail, featureIndex, detailIndex) {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return `<div><input data-feature-detail="${featureIndex}:${detailIndex}" value="${esc(detail || '')}" placeholder="Enter a detail"><button class="icon-btn" type="button" data-feature-detail-remove="${featureIndex}:${detailIndex}" title="Delete detail">×</button></div>`;
  const rows = Object.entries(detail).map(([key,value], keyIndex) => `<label><span>${esc(key.replaceAll('_',' ').trim())}</span><textarea data-feature-object-value="${featureIndex}:${detailIndex}:${keyIndex}" rows="1">${esc(structuredValueText(value))}</textarea></label>`).join('');
  return `<div class="feature-object-detail"><div class="feature-object-heading"><strong>Structured record</strong><button class="icon-btn" type="button" data-feature-detail-remove="${featureIndex}:${detailIndex}" title="Delete record">×</button></div><div class="feature-object-fields">${rows}</div></div>`;
}
function renderFeatureDetailReview(detail) {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return `<p>${esc(detail || '')}</p>`;
  return `<dl class="feature-record-review">${Object.entries(detail).map(([key,value]) => `<div><dt>${esc(key.replaceAll('_',' ').trim())}</dt><dd>${esc(value === null ? '—' : typeof value === 'object' ? Object.entries(value).map(([nestedKey,nestedValue]) => `${nestedKey}: ${nestedValue}`).join(' · ') : value)}</dd></div>`).join('')}</dl>`;
}

function renderServicesEditor(data) {
  const services = data.services;
  if (state.workflowMode === 'review') {
    const cards = services.map((service, index) => `<article class="reference-entry review-entry"><header><span class="entry-kind">SERVICE ${index + 1}</span><strong>${esc(service.service || 'Unnamed service')}</strong></header><div class="reference-review-block"><span>Service details</span>${(Array.isArray(service.description) ? service.description : []).map(detail => `<p>${esc(detail || '')}</p>`).join('') || '<p>No details entered</p>'}</div></article>`).join('');
    $('#visual').innerHTML = `<div class="features-editor">${contentModeHeader(true, {reviewTitle: 'Review services', reviewBody: 'Review all services, then select Approve all.'})}<div class="section-title"><div><p class="eyebrow">SERVICES</p><h3>${services.length} services</h3></div></div><div class="reference-list">${cards}</div></div>`;
    $('#approve').hidden = !state.me.approver; $('#save').hidden = true;
    document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); }); return;
  }
  const cards = services.map((service, serviceIndex) => `<article class="feature-entry"><header><label>Service name<input data-service-name="${serviceIndex}" value="${esc(service.service || '')}" placeholder="Enter service name"></label><button class="icon-btn" type="button" data-service-remove="${serviceIndex}" title="Delete service">×</button></header><div class="feature-details"><strong>Service details</strong>${(Array.isArray(service.description) ? service.description : []).map((detail, detailIndex) => `<div><input data-service-detail="${serviceIndex}:${detailIndex}" value="${esc(detail || '')}" placeholder="Enter a detail"><button class="icon-btn" type="button" data-service-detail-remove="${serviceIndex}:${detailIndex}" title="Delete detail">×</button></div>`).join('')}<button class="secondary feature-add-detail" type="button" data-service-detail-add="${serviceIndex}">+ Add detail</button></div></article>`).join('');
  $('#visual').innerHTML = `<div class="features-editor">${contentModeHeader(false, {editLead: 'Update the services.'})}<div class="form-card overview-description service-overview"><label>Overview section<input id="serviceSection" value="${esc(data.section || '')}" placeholder="Application feature overview"></label><label>Category<input id="serviceType" value="${esc(data.type || '')}" placeholder="For example: Transfers"></label></div><div class="section-title"><div><p class="eyebrow">SERVICES</p><h3>${services.length} services</h3></div><button id="addService" class="secondary">+ Add service</button></div><div class="feature-list">${cards || '<p class="no-results">No services yet. Add the first service.</p>'}</div></div>`;
  $('#approve').hidden = true; $('#save').hidden = false;
  document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; renderEditor(); });
  $('#serviceSection').oninput = event => { data.section = event.target.value; setDirty(true); };
  $('#serviceType').oninput = event => { data.type = event.target.value; setDirty(true); };
  $('#addService').onclick = () => { services.push({service:'', description:['']}); setDirty(true); renderServicesEditor(data); };
  document.querySelectorAll('[data-service-name]').forEach(input => input.oninput = () => { data.services[+input.dataset.serviceName].service = input.value; setDirty(true); });
  document.querySelectorAll('[data-service-detail]').forEach(input => input.oninput = () => { const [serviceIndex, detailIndex] = input.dataset.serviceDetail.split(':').map(Number); data.services[serviceIndex].description[detailIndex] = input.value; setDirty(true); });
  document.querySelectorAll('[data-service-remove]').forEach(button => button.onclick = () => { data.services.splice(+button.dataset.serviceRemove, 1); setDirty(true); renderServicesEditor(data); });
  document.querySelectorAll('[data-service-detail-remove]').forEach(button => button.onclick = () => { const [serviceIndex, detailIndex] = button.dataset.serviceDetailRemove.split(':').map(Number); data.services[serviceIndex].description.splice(detailIndex, 1); setDirty(true); renderServicesEditor(data); });
  document.querySelectorAll('[data-service-detail-add]').forEach(button => button.onclick = () => { data.services[+button.dataset.serviceDetailAdd].description.push(''); setDirty(true); renderServicesEditor(data); });
}

function workflowStepImages(step) {
  return String(step.image || '').split(',').map(value => value.trim()).filter(Boolean);
}

function startWorkflowWizard(data) {
  data.steps.push({step: data.steps.length + 1, text: '', image: null});
  state.workflowWizard = {current: data.steps.length - 1, phase: 'editing'};
}

function renderWorkflowWizardStep(data) {
  const wizard = state.workflowWizard;
  const index = wizard.current;
  const step = data.steps[index];
  const images = workflowStepImages(step);
  const gallery = images.map((image, imageIndex) => `<div class="shot"><a href="${imageUrl(image)}" target="_blank" title="Open full size"><img src="${imageUrl(image)}" alt="${esc(image)}" loading="lazy"></a><button type="button" data-wizard-image-remove="${imageIndex}" title="Remove image">×</button></div>`).join('');
  return `<section class="retail-wizard">
    <div class="retail-wizard-progress"><span>${esc(data.title || 'New workflow')}</span><small>Step ${index + 1}</small></div>
    <div class="retail-wizard-head"><p class="eyebrow">WORKFLOW STEP ${index + 1}</p><h2>What happens in this step?</h2><p>Describe the instruction as it should appear to the reader.</p></div>
    <div class="form-card"><label>Step text<textarea id="workflowWizardText" placeholder="For example: Open the Capital Mobile app and tap Transfers">${esc(step.text || '')}</textarea></label><div class="image-tools"><label class="image-add">+ Add image<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" id="workflowWizardImage" hidden></label><span>Optional — add a screenshot for this step.</span></div>${gallery ? `<div class="shots">${gallery}</div>` : ''}</div>
    <div class="retail-wizard-actions"><button class="secondary" id="workflowWizardExit">Cancel</button><div class="retail-wizard-actions-right">${index > 0 ? '<button class="secondary" id="workflowWizardBack">← Back</button>' : ''}${data.steps.length > 1 ? '<button class="secondary" id="workflowWizardRemove">Remove step</button>' : ''}<button class="secondary" id="workflowWizardAddAnother">+ Add another step</button><button id="workflowWizardReview">Review workflow →</button></div></div>
  </section>`;
}

function renderWorkflowWizardReview(data) {
  const rows = data.steps.map((step, index) => {
    const text = String(step.text || '').trim();
    const preview = text ? (text.length > 90 ? `${text.slice(0, 90)}…` : text) : 'No text yet';
    return `<div class="retail-wizard-review-row ${text ? '' : 'skipped'}"><div><strong>Step ${index + 1}</strong><small>${esc(preview)}</small></div><div class="retail-wizard-review-row-actions"><button class="secondary" data-wizard-step-edit="${index}">Edit</button>${data.steps.length > 1 ? `<button class="secondary" data-wizard-step-remove="${index}">Remove</button>` : ''}</div></div>`;
  }).join('');
  return `<section class="retail-wizard retail-wizard-review">
    <div class="retail-wizard-progress"><span>${esc(data.title || 'New workflow')}</span><small>Review</small></div>
    <div class="retail-wizard-head"><p class="eyebrow">REVIEW</p><h2>Check the workflow</h2><p>${data.steps.length} step${data.steps.length === 1 ? '' : 's'} will be saved.</p></div>
    <div class="retail-wizard-review-list">${rows}</div>
    <div class="retail-wizard-actions"><button class="secondary" id="workflowWizardAddStep">+ Add another step</button><div class="retail-wizard-actions-right"><button class="secondary" id="workflowWizardExitAdvanced">Continue editing</button><button id="workflowWizardSubmit" class="${state.me.approver ? 'submit-approve' : ''}">${state.me.approver ? 'Submit and approve' : 'Submit changes'}</button></div></div>
  </section>`;
}

function renderWorkflowWizard(data) {
  return state.workflowWizard.phase === 'review' ? renderWorkflowWizardReview(data) : renderWorkflowWizardStep(data);
}

function wireWorkflowWizard(data) {
  const wizard = state.workflowWizard;
  if (wizard.phase === 'review') {
    document.querySelectorAll('[data-wizard-step-edit]').forEach(button => button.onclick = () => { wizard.current = +button.dataset.wizardStepEdit; wizard.phase = 'editing'; renderEditor(); });
    document.querySelectorAll('[data-wizard-step-remove]').forEach(button => button.onclick = () => {
      data.steps.splice(+button.dataset.wizardStepRemove, 1); data.steps.forEach((item, i) => { if (Number.isInteger(item.step)) item.step = i + 1; });
      invalidateStepReviews(data); renderEditor();
    });
    if ($('#workflowWizardAddStep')) $('#workflowWizardAddStep').onclick = () => { startWorkflowWizard(data); renderEditor(); };
    if ($('#workflowWizardExitAdvanced')) $('#workflowWizardExitAdvanced').onclick = () => { state.workflowWizard = null; state.workflowWizardDismissed = true; renderEditor(); };
    if ($('#workflowWizardSubmit')) $('#workflowWizardSubmit').onclick = async () => {
      const button = $('#workflowWizardSubmit'); button.disabled = true;
      try { state.workflowWizard = null; await save(); } finally { if (button) button.disabled = false; }
    };
    return;
  }
  const index = wizard.current;
  const step = data.steps[index];
  if ($('#workflowWizardText')) $('#workflowWizardText').oninput = event => { step.text = event.target.value; invalidateReview(data, `steps.${index}.text`); };
  if ($('#workflowWizardExit')) $('#workflowWizardExit').onclick = () => {
    if (!String(step.text || '').trim()) { data.steps.splice(index, 1); data.steps.forEach((item, i) => { if (Number.isInteger(item.step)) item.step = i + 1; }); }
    state.workflowWizard = null; state.workflowWizardDismissed = true; renderEditor();
  };
  if ($('#workflowWizardBack')) $('#workflowWizardBack').onclick = () => { wizard.current -= 1; renderEditor(); };
  if ($('#workflowWizardRemove')) $('#workflowWizardRemove').onclick = () => {
    data.steps.splice(index, 1); data.steps.forEach((item, i) => { if (Number.isInteger(item.step)) item.step = i + 1; });
    wizard.current = Math.min(index, data.steps.length - 1); invalidateStepReviews(data); renderEditor();
  };
  if ($('#workflowWizardAddAnother')) $('#workflowWizardAddAnother').onclick = () => {
    if (!String(step.text || '').trim()) return toast('Describe this step before adding another');
    startWorkflowWizard(data); renderEditor();
  };
  if ($('#workflowWizardReview')) $('#workflowWizardReview').onclick = () => {
    if (!String(step.text || '').trim()) return toast('Describe this step before continuing');
    wizard.phase = 'review'; renderEditor();
  };
  if ($('#workflowWizardImage')) $('#workflowWizardImage').onchange = () => uploadStepImage(index, $('#workflowWizardImage').files[0]);
  document.querySelectorAll('[data-wizard-image-remove]').forEach(button => button.onclick = () => {
    const images = workflowStepImages(step); images.splice(+button.dataset.wizardImageRemove, 1); step.image = images.length ? images.join(', ') : null;
    invalidateReview(data, `steps.${index}.image`); renderEditor();
  });
}

function renderEditor() {
  $('#approve').textContent = 'Approve all';
  const data = state.selected.data; $('#raw').value = JSON.stringify(data, null, 2); $('#raw').hidden = !state.raw; $('#visual').hidden = state.raw; if (state.raw) return;
  const steps = Array.isArray(data.steps) ? data.steps : null;
  if (!steps) {
    if (Array.isArray(data) && data.every(item => item && typeof item === 'object' && ('question' in item || 'answer' in item))) { renderFaqEditor(data); return; }
    if (data && typeof data === 'object' && Array.isArray(data.tips)) { renderTipsEditor(data); return; }
    if (data && typeof data === 'object' && Array.isArray(data.Rules)) { renderRulesEditor(data); return; }
    if (data && typeof data === 'object' && Array.isArray(data.overview)) { renderOverviewEditor(data); return; }
    if (data && typeof data === 'object' && Array.isArray(data.features)) { renderFeaturesEditor(data); return; }
    if (data && typeof data === 'object' && Array.isArray(data.services)) { renderServicesEditor(data); return; }
    $('#visual').innerHTML = `<div class="form-card"><label>Structured JSON<textarea class="large" id="referenceJson">${esc(JSON.stringify(data, null, 2))}</textarea></label></div>`; return;
  }
  const reviewing = state.workflowMode === 'review';
  if (!reviewing && !state.workflowWizard && !state.workflowWizardDismissed && steps.length === 0) startWorkflowWizard(data);
  if (!reviewing && state.workflowWizard) {
    $('#save').hidden = true; $('#approve').hidden = true;
    $('#visual').innerHTML = renderWorkflowWizard(data);
    wireWorkflowWizard(data);
    return;
  }
  const header = contentModeHeader(reviewing, {reviewTitle: 'Review workflow', reviewBody: 'Review the content below, then select Approve all when the workflow is ready.', editLead: 'Update the workflow text and images.'});
  const identity = reviewing ? `<div class="form-card workflow-identity review-identity workflow-title-only"><div><label>Workflow title</label><strong>${esc(data.title)}</strong></div></div>` : `<div class="form-card workflow-identity"><div><label>Workflow title<input id="title" value="${esc(data.title)}"></label></div></div>`;
  $('#visual').innerHTML = `${header}${identity}<div class="section-title"><div><p class="eyebrow">WORKFLOW CONTENT</p><h3>${steps.length} entries</h3></div>${reviewing ? '' : '<button id="addStep" class="secondary">+ Add entry</button>'}</div><div id="steps">${steps.map(entry).join('')}</div>`;
  $('#approve').hidden = !(state.me.approver && reviewing); $('#save').hidden = reviewing;
  document.querySelectorAll('[data-workflow-mode]').forEach(button => button.onclick = () => { state.workflowMode = button.dataset.workflowMode; state.raw = false; renderEditor(); });
  if (!reviewing) {
    $('#addStep').onclick = () => { startWorkflowWizard(data); renderEditor(); };
    $('#title').oninput = event => { data.title = event.target.value; invalidateReview(data, 'title'); };
    document.querySelectorAll('[data-text]').forEach(input => input.oninput = () => { const index = +input.dataset.text; data.steps[index].text = input.value; invalidateReview(data, `steps.${index}.text`); });
    document.querySelectorAll('[data-label]').forEach(input => input.oninput = () => { const index = +input.dataset.label; const step = data.steps[index]; step['Note' in step ? 'Note' : 'step'] = input.value; invalidateReview(data, `steps.${index}.label`); });
  }
  document.querySelectorAll('[data-remove]').forEach(button => button.onclick = () => { data.steps.splice(+button.dataset.remove, 1); invalidateStepReviews(data); renderEditor(); });
  document.querySelectorAll('[data-substep-add]').forEach(button => button.onclick = () => { const parent = data.steps[+button.dataset.substepAdd]; const children = parent.substeps ||= []; children.push({step: `${parent.step}.${String.fromCharCode(97 + children.length)}`, text: '', image: null}); invalidateStepReviews(data); renderEditor(); });
  document.querySelectorAll('[data-substep-remove]').forEach(button => button.onclick = () => { const [stepIndex, substepIndex] = button.dataset.substepRemove.split(':').map(Number); data.steps[stepIndex].substeps.splice(substepIndex, 1); invalidateStepReviews(data); renderEditor(); });
  document.querySelectorAll('[data-substep-text]').forEach(input => input.oninput = () => { const [stepIndex, substepIndex] = input.dataset.substepText.split(':').map(Number); data.steps[stepIndex].substeps[substepIndex].text = input.value; invalidateStepReviews(data); });
  document.querySelectorAll('[data-substep-label]').forEach(input => input.oninput = () => { const [stepIndex, substepIndex] = input.dataset.substepLabel.split(':').map(Number); data.steps[stepIndex].substeps[substepIndex].step = input.value; invalidateStepReviews(data); });
  document.querySelectorAll('[data-image-remove]').forEach(button => button.onclick = () => {
    const step = data.steps[+button.dataset.imageRemove];
    const images = String(step.image || '').split(',').map(value => value.trim()).filter(Boolean);
    images.splice(+button.dataset.imageIndex, 1); step.image = images.length ? images.join(', ') : null; invalidateReview(data, `steps.${+button.dataset.imageRemove}.image`); renderEditor();
  });
  document.querySelectorAll('[data-image-add]').forEach(input => input.onchange = () => uploadStepImage(+input.dataset.imageAdd, input.files[0]));
}
function collect() { if (state.raw) return JSON.parse($('#raw').value); if ($('#referenceJson')) return JSON.parse($('#referenceJson').value); if ($('#title')) state.selected.data.title = $('#title').value.trim(); if (state.selected.data && !Array.isArray(state.selected.data) && Array.isArray(state.selected.data.steps)) state.selected.data.login_stage = topicStageForPath(state.selected.path); return state.selected.data; }

function retailPath(section, data, index) {
  const stored = Array.isArray(section?.path) ? section.path.map(value => String(value || '').trim()).filter(Boolean) : [];
  if (stored.length) return stored;
  const legacy = String(section?.section_title || '').split('>').map(value => value.trim()).filter(Boolean);
  if (legacy.length) return legacy.map((value, partIndex) => partIndex === legacy.length - 1 ? value.replace(/^[•*\-]+\s*/, '').replace(/:$/, '') : value.replace(/:$/, ''));
  return [String(data?.title || 'Document'), String(section?.section || `Section ${index + 1}`).replace(/:$/, '')];
}

function retailNorm(value) { return String(value || '').trim().replace(/[:\s]+$/, '').toLowerCase(); }

// Finds where a section's real category starts by skipping any leading path
// segments that just echo the document title or repeat the segment before
// them (extraction sometimes nests "Doc > Doc > Doc > Subtype > ..."). The
// first segment left after that skip is the category boundary; everything
// before it (inclusive) is offset away when building the in-category tree.
function retailCategoryBoundary(path) {
  if (path.length <= 2) return path.length - 1;
  const docNorm = retailNorm(path[0]);
  let prevNorm = docNorm;
  for (let i = 1; i < path.length - 1; i++) {
    const n = retailNorm(path[i]);
    if (n === docNorm || n === prevNorm) { prevNorm = n; continue; }
    return i;
  }
  return path.length - 1;
}

function retailCategoryFor(section, data, index) {
  const path = retailPath(section, data, index);
  const boundary = retailCategoryBoundary(path);
  if (boundary < path.length - 1) return path[boundary].replace(/:$/, '');
  return retailNorm(path.at(-1)) === 'description' ? 'Description' : 'Overview';
}

function retailCategories(data) {
  const structuredChildren = Array.isArray(data?.structure?.children) ? data.structure.children : null;
  if (structuredChildren) {
    const structured = new Map(); let indexed = 0;
    const indexesFor = node => { const indexes = []; const visit = child => { if (Number.isInteger(child?.section_index)) { indexes.push(child.section_index); indexed++; } (child?.children || []).forEach(visit); }; visit(node); return indexes; };
    const overview = [];
    structuredChildren.forEach(node => {
      if (node.type === 'category') structured.set(String(node.title || 'Content'), indexesFor(node));
      else if (retailNorm(node.title) === 'description') structured.set('Description', indexesFor(node));
      else overview.push(...indexesFor(node));
    });
    if (overview.length) structured.set('Overview', overview);
    if (indexed === data.sections.length) return structured;
  }
  const categories = new Map();
  data.sections.forEach((section, index) => {
    const name = retailCategoryFor(section, data, index);
    if (!categories.has(name)) categories.set(name, []);
    categories.get(name).push(index);
  });
  return categories;
}

function retailCategorySummary(data, name, indexes) {
  const node = (data?.structure?.children || []).find(child => child?.type === 'category' && String(child.title) === name);
  if (name === 'Terms and Conditions' && node) return `${node.children?.length || 0} terms`;
  return `${indexes.length} section${indexes.length === 1 ? '' : 's'}`;
}

function retailCategoryCount(data, name, indexes) {
  const node = (data?.structure?.children || []).find(child => child?.type === 'category' && String(child.title) === name);
  return name === 'Terms and Conditions' && node ? node.children?.length || 0 : indexes.length;
}

function retailGlobalMatches(data, categories, query) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const results = [];
  data.sections.forEach((section, index) => {
    const path = retailPath(section, data, index);
    const title = String(path.at(-1) || '');
    if (!title.toLowerCase().includes(q)) return;
    results.push({index, title, categoryName: retailCategoryFor(section, data, index)});
  });
  return results.slice(0, 40);
}

function retailTree(data, category, changedByIndex = new Map(), query = '', sectionEditorHtml = '', groupEditorHtml = '') {
  const root = {children:new Map(), sections:[]};
  const allowedIndexes = retailCategories(data).get(category) || [];
  data.sections.forEach((section, index) => {
    if (!allowedIndexes.includes(index)) return;
    const path = retailPath(section, data, index); let node = root;
    const categoryOffset = Math.min(retailCategoryBoundary(path) + 1, Math.max(0, path.length - 1));
    path.slice(categoryOffset, -1).forEach(part => { if (!node.children.has(part)) node.children.set(part, {children:new Map(), sections:[]}); node = node.children.get(part); });
    node.sections.push({section, index, title:path.at(-1)});
  });
  const mergeParentSections = node => {
    node.children.forEach((child, label) => {
      const sectionIndex = node.sections.findIndex(item => retailNorm(item.title) === retailNorm(label));
      if (sectionIndex >= 0) child.sections.unshift({...node.sections.splice(sectionIndex, 1)[0], title:'Overview'});
      mergeParentSections(child);
    });
  };
  mergeParentSections(root);
  const q = query.trim().toLowerCase();
  const matches = title => !q || title.toLowerCase().includes(q);
  const hasMatch = node => node.sections.some(item => matches(item.title)) || [...node.children.values()].some(hasMatch);
  const containsSelected = node => node.sections.some(item => item.index === state.retailSection) || [...node.children.values()].some(containsSelected);
  const nodeCount = node => node.sections.length + [...node.children.values()].reduce((total, child) => total + nodeCount(child), 0);
  const parentSection = node => node.sections.find(item => retailNorm(item.title) === 'overview') || null;
  const nodeFirstIndex = node => Math.min(...node.sections.map(item => item.index), ...[...node.children.values()].map(nodeFirstIndex), Number.MAX_SAFE_INTEGER);
  const isSectionOpen = index => !Array.isArray(state.retailGroupPath) && index === state.retailSection;
  const sectionRow = item => {
    const open = isSectionOpen(item.index);
    const row = `<button class="retail-outline-row ${open ? 'active' : ''} ${changedByIndex.has(item.index) ? 'needs-review' : ''}" data-retail-open="${item.index}" aria-expanded="${open}"><span class="retail-outline-chevron">${open ? '⌄' : '›'}</span><span class="retail-outline-label"><strong>${esc(item.title)}</strong>${changedByIndex.has(item.index) ? '<em>Review</em>' : ''}</span></button>`;
    return `<div class="retail-outline-item ${open ? 'open' : ''}">${row}${open ? sectionEditorHtml : ''}</div>`;
  };
  const draw = (node, depth = 0, hiddenSectionIndex = null, trail = []) => {
    const visibleSections = node.sections.filter(item => item.index !== hiddenSectionIndex);
    const entries = [
      ...[...node.children].filter(([label, child]) => {
        const currentGroupPath = [data.title, category, ...trail, label];
        const isOpenAncestor = Array.isArray(state.retailGroupPath) && state.retailGroupPath.length >= currentGroupPath.length && currentGroupPath.every((part, i) => retailNorm(part) === retailNorm(state.retailGroupPath[i]));
        return matches(label) || hasMatch(child) || containsSelected(child) || isOpenAncestor;
      }).map(([label, child]) => ({type:'child', label, child, index:nodeFirstIndex(child)})),
      ...visibleSections.filter(item => matches(item.title) || item.index === state.retailSection).map(item => ({type:'section', item, index:item.index})),
    ].sort((a,b) => a.index-b.index);
    return entries.map(entry => {
      if (entry.type === 'section') return sectionRow(entry.item);
      const parent = parentSection(entry.child);
      const childCount = Math.max(0, nodeCount(entry.child) - (parent ? 1 : 0));
      const currentGroupPath = [data.title, category, ...trail, entry.label];
      const groupSelected = Array.isArray(state.retailGroupPath) && state.retailGroupPath.length === currentGroupPath.length && currentGroupPath.every((part, i) => retailNorm(part) === retailNorm(state.retailGroupPath[i]));
      const parentOpen = parent && isSectionOpen(parent.index);
      const title = parent
        ? `<button class="retail-outline-title ${parentOpen ? 'active' : ''} ${changedByIndex.has(parent.index) ? 'needs-review' : ''}" data-retail-open="${parent.index}" title="Open and edit ${esc(entry.label)}"><strong>${esc(entry.label)}</strong>${changedByIndex.has(parent.index) ? '<em>Review</em>' : ''}</button>`
        : `<button class="retail-outline-title ${groupSelected ? 'active' : ''}" data-retail-group="${esc(encodeURIComponent(JSON.stringify(currentGroupPath)))}" title="Open ${esc(entry.label)}"><strong>${esc(entry.label)}</strong></button>`;
      const inlineEditor = parentOpen ? sectionEditorHtml : (groupSelected ? groupEditorHtml : '');
      return `<details class="retail-outline-group depth-${Math.min(depth, 3)}" ${containsSelected(entry.child) || groupSelected || q ? 'open' : ''}><summary>${title}<small>${childCount} sub-section${childCount === 1 ? '' : 's'}</small></summary>${inlineEditor}${draw(entry.child, depth + 1, parent?.index ?? null, [...trail, entry.label])}</details>`;
    }).join('');
  };
  const noResultsNotice = q && !hasMatch(root) ? '<p class="no-results">No sections match “' + esc(query.trim()) + '”.</p>' : '';
  return noResultsNotice + draw(root);
}
function ensureContentBlocks(section) {
  if (Array.isArray(section.content_blocks) && section.content_blocks.length) return section.content_blocks;
  section.content_blocks = [{type:'paragraph', text:String(section.content || '')}]; return section.content_blocks;
}

function retailDisplayLabel(block, title) {
  const label = String(block.label || '').trim();
  return !label || label === 'Items' || label === 'Steps' ? title : label;
}

function renderRetailReadView(section, title, reviewing) {
  return `<div class="structured-content retail-read-view ${reviewing ? 'retail-review-content' : ''}"><div class="structured-content-head"><strong>Content</strong><span>${reviewing ? 'Read-only approval preview' : 'Reading view — select Edit content to make changes.'}</span></div>${ensureContentBlocks(section).map(block => block.type === 'paragraph'
    ? `<article class="review-content-block"><p>${esc(block.text || '')}</p></article>`
    : `<article class="review-content-block"><strong>${esc(retailDisplayLabel(block, title))}</strong><${block.type === 'numbered_list' ? 'ol' : 'ul'}>${(block.items || []).map(item => `<li>${esc(item)}</li>`).join('')}</${block.type === 'numbered_list' ? 'ol' : 'ul'}></article>`).join('')}</div>`;
}

function renderRetailContent(section, reviewing = false, title = '') {
  if (section?.variant === 'table') return renderEditableTable(section);
  if (reviewing) return renderRetailReadView(section, title, reviewing);
  return `<div class="structured-content"><div class="structured-content-head"><strong>Content</strong></div>${ensureContentBlocks(section).map((block, blockIndex) => block.type === 'paragraph'
    ? `<section class="content-block"><header><strong>Text</strong><button class="icon-btn" data-retail-block-remove="${blockIndex}" title="Delete text block">×</button></header><textarea data-retail-paragraph="${blockIndex}" placeholder="Enter normal text">${esc(block.text || '')}</textarea></section>`
    : `<section class="content-block list-block"><header><strong>${block.type === 'numbered_list' ? 'Numbered list' : 'Bullet list'}</strong><button class="icon-btn" data-retail-block-remove="${blockIndex}" title="Delete list">×</button></header><label>Optional list heading<input data-retail-list-label="${blockIndex}" value="${esc(block.label || '')}" placeholder="For example: Benefits"></label>${(block.items || []).map((item, itemIndex) => `<div class="list-item-row"><span>${block.type === 'numbered_list' ? itemIndex + 1 + '.' : '•'}</span><textarea data-retail-list-item="${blockIndex}:${itemIndex}" placeholder="Enter item">${esc(item)}</textarea><button class="icon-btn" data-retail-list-remove="${blockIndex}:${itemIndex}" title="Delete item">×</button></div>`).join('')}<button class="secondary" data-retail-list-add="${blockIndex}">+ Add item</button></section>`).join('')}<div class="content-block-actions"><span>Add another content block:</span><button class="secondary" data-retail-block-add="paragraph">+ Text</button><button class="secondary" data-retail-block-add="bullet_list">+ Bullet list</button><button class="secondary" data-retail-block-add="numbered_list">+ Numbered list</button></div></div>`;
}

function syncRetailContent(section) {
  section.content = ensureContentBlocks(section).map(block => block.type === 'paragraph' ? String(block.text || '').trim() : `${block.label ? String(block.label).trim() + ':\n' : ''}${(block.items || []).map((item, index) => block.type === 'numbered_list' ? `${index + 1}. ${String(item).trim()}` : `• ${String(item).trim()}`).join('\n')}`).filter(Boolean).join('\n\n');
}

const RETAIL_CONTENT_TAG_SUGGESTIONS = ['Overview', 'Description', 'Eligibility', 'Fees & Charges', 'Required Documents', 'Terms & Conditions', 'Loan Types', 'FAQs'];
const RETAIL_WIZARD_TOPICS = ['Description', 'Eligibility', 'Fees & Charges', 'Required Documents', 'Terms & Conditions', 'FAQs'];
const RETAIL_WIZARD_HINTS = {
  'Description': 'A short overview of what this product is and who it is for.',
  'Eligibility': 'Who can apply — for example income, age, or required documentation.',
  'Fees & Charges': 'Any costs, interest rates, or charges customers should know about.',
  'Required Documents': 'Documents a customer needs to provide to apply.',
  'Terms & Conditions': 'Key terms customers must agree to.',
  'FAQs': 'Common questions customers ask about this product.',
};

function retailWizardBlocksHaveContent(blocks) {
  return (blocks || []).some(block => block.type === 'paragraph' ? String(block.text || '').trim() : (block.items || []).some(item => String(item || '').trim()));
}

function retailWizardPreviewText(draft) {
  const block = (draft?.content_blocks || []).find(item => item.type === 'paragraph' ? String(item.text || '').trim() : (item.items || []).some(value => String(value || '').trim()));
  if (!block) return '';
  const text = block.type === 'paragraph' ? String(block.text || '').trim() : (block.items || []).filter(Boolean).join(', ');
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}

function startRetailContentWizard(data, categories) {
  const existing = new Set([...categories.keys()].map(retailNorm));
  const topics = RETAIL_WIZARD_TOPICS.filter(topic => !existing.has(retailNorm(topic)));
  if (!topics.length) return false;
  const brandNew = categories.size === 0;
  state.retailWizard = {topics, mandatoryCount: brandNew ? Math.min(2, topics.length) : 0, finalizeAction: brandNew ? 'create' : 'add', stepIndex: 0, drafts: {}, skipped: new Set()};
  return true;
}

function retailWizardDraft(topic) {
  const wizard = state.retailWizard;
  if (!wizard.drafts[topic]) wizard.drafts[topic] = {content_blocks: [{type: 'paragraph', text: ''}]};
  return wizard.drafts[topic];
}

function renderRetailWizardStep(data) {
  const wizard = state.retailWizard;
  const topic = wizard.topics[wizard.stepIndex];
  const mandatory = wizard.stepIndex < wizard.mandatoryCount;
  const draft = retailWizardDraft(topic);
  const blocks = ensureContentBlocks(draft);
  const blocksHtml = blocks.map((block, blockIndex) => block.type === 'paragraph'
    ? `<section class="content-block"><header><strong>Text</strong><button class="icon-btn" data-wizard-block-remove="${blockIndex}" title="Delete text block">×</button></header><textarea data-wizard-paragraph="${blockIndex}" placeholder="Enter normal text">${esc(block.text || '')}</textarea></section>`
    : `<section class="content-block list-block"><header><strong>${block.type === 'numbered_list' ? 'Numbered list' : 'Bullet list'}</strong><button class="icon-btn" data-wizard-block-remove="${blockIndex}" title="Delete list">×</button></header><label>Optional list heading<input data-wizard-list-label="${blockIndex}" value="${esc(block.label || '')}" placeholder="For example: Benefits"></label>${(block.items || []).map((item, itemIndex) => `<div class="list-item-row"><span>${block.type === 'numbered_list' ? itemIndex + 1 + '.' : '•'}</span><textarea data-wizard-list-item="${blockIndex}:${itemIndex}" placeholder="Enter item">${esc(item)}</textarea><button class="icon-btn" data-wizard-list-remove="${blockIndex}:${itemIndex}" title="Delete item">×</button></div>`).join('')}<button class="secondary" data-wizard-list-add="${blockIndex}">+ Add item</button></section>`).join('');
  return `<section class="retail-wizard">
    <div class="retail-wizard-progress"><span>${esc(data.title)}</span><small>Step ${wizard.stepIndex + 1} of ${wizard.topics.length + 1}</small></div>
    <div class="retail-wizard-head"><p class="eyebrow">${mandatory ? 'REQUIRED' : 'OPTIONAL'}</p><h2>${esc(topic)}</h2><p>${esc(RETAIL_WIZARD_HINTS[topic] || '')}</p></div>
    <div class="structured-content">${blocksHtml}<div class="content-block-actions"><span>Add another content block:</span><button class="secondary" data-wizard-block-add="paragraph">+ Text</button><button class="secondary" data-wizard-block-add="bullet_list">+ Bullet list</button><button class="secondary" data-wizard-block-add="numbered_list">+ Numbered list</button></div></div>
    <div class="retail-wizard-actions"><button class="secondary" id="retailWizardExit">Cancel</button><div class="retail-wizard-actions-right">${wizard.stepIndex > 0 ? '<button class="secondary" id="retailWizardBack">← Back</button>' : ''}${mandatory ? '' : '<button class="secondary" id="retailWizardSkip">Skip</button>'}<button id="retailWizardNext">${wizard.stepIndex === wizard.topics.length - 1 ? 'Review' : 'Next'} →</button></div></div>
  </section>`;
}

function renderRetailWizardReview(data) {
  const wizard = state.retailWizard;
  const rows = wizard.topics.map(topic => {
    const draft = wizard.drafts[topic];
    const filled = !wizard.skipped.has(topic) && draft && retailWizardBlocksHaveContent(draft.content_blocks);
    const preview = filled ? esc(retailWizardPreviewText(draft)) : 'Not added';
    return `<div class="retail-wizard-review-row ${filled ? '' : 'skipped'}"><div><strong>${esc(topic)}</strong><small>${preview}</small></div><button class="secondary" data-wizard-review-edit="${esc(topic)}">${filled ? 'Edit' : 'Add'}</button></div>`;
  }).join('');
  return `<section class="retail-wizard retail-wizard-review">
    <div class="retail-wizard-progress"><span>${esc(data.title)}</span><small>Step ${wizard.topics.length + 1} of ${wizard.topics.length + 1}</small></div>
    <div class="retail-wizard-head"><p class="eyebrow">REVIEW</p><h2>Check the content</h2><p>Everything below will be added to ${esc(data.title)}. You can still edit or add anything you skipped.</p></div>
    <div class="retail-wizard-review-list">${rows}</div>
    <div class="retail-wizard-actions"><button class="secondary" id="retailWizardExit">Cancel</button><div class="retail-wizard-actions-right"><button class="secondary" id="retailWizardBack">← Back</button><button id="retailWizardFinish" class="${state.me.approver ? 'submit-approve' : ''}">${state.me.approver ? 'Submit and approve' : 'Submit changes'}</button></div></div>
  </section>`;
}

function renderRetailWizard(data) {
  return state.retailWizard.stepIndex >= state.retailWizard.topics.length ? renderRetailWizardReview(data) : renderRetailWizardStep(data);
}

function wireRetailWizard(data) {
  const wizard = state.retailWizard;
  if ($('#retailWizardExit')) $('#retailWizardExit').onclick = () => { state.retailWizard = null; state.retailWizardDismissed = true; renderRetailEditor(); };
  if (wizard.stepIndex >= wizard.topics.length) {
    if ($('#retailWizardBack')) $('#retailWizardBack').onclick = () => { wizard.stepIndex = wizard.topics.length - 1; renderRetailEditor(); };
    document.querySelectorAll('[data-wizard-review-edit]').forEach(button => button.onclick = () => { const topic = button.dataset.wizardReviewEdit; wizard.skipped.delete(topic); wizard.stepIndex = wizard.topics.indexOf(topic); renderRetailEditor(); });
    if ($('#retailWizardFinish')) $('#retailWizardFinish').onclick = async () => {
      const button = $('#retailWizardFinish'); button.disabled = true;
      try {
        let addedAny = false;
        wizard.topics.forEach(topic => {
          if (wizard.skipped.has(topic)) return;
          const draft = wizard.drafts[topic];
          if (!draft || !retailWizardBlocksHaveContent(draft.content_blocks)) return;
          const newPath = [data.title, topic, 'Description'];
          const section = {topic: data.title, section: 'Description', variant: 'description', section_title: newPath.join(' > '), path: newPath, display_title: 'Description', language: 'en', images: [], content_blocks: draft.content_blocks};
          syncRetailContent(section); data.sections.push(section); addedAny = true;
        });
        const finalizeAction = wizard.finalizeAction;
        state.retailWizard = null;
        if (finalizeAction === 'create') { setDirty(true); await save(); }
        else if (addedAny) { setDirty(true); state.retailCategory = null; renderRetailEditor(); toast('Content added. Submit when ready.'); }
        else { renderRetailEditor(); }
      } finally { if (button) button.disabled = false; }
    };
    return;
  }
  const topic = wizard.topics[wizard.stepIndex];
  const mandatory = wizard.stepIndex < wizard.mandatoryCount;
  const draft = retailWizardDraft(topic);
  if ($('#retailWizardBack')) $('#retailWizardBack').onclick = () => { wizard.stepIndex -= 1; renderRetailEditor(); };
  if ($('#retailWizardSkip')) $('#retailWizardSkip').onclick = () => { wizard.skipped.add(topic); wizard.stepIndex += 1; renderRetailEditor(); };
  if ($('#retailWizardNext')) $('#retailWizardNext').onclick = () => {
    if (mandatory && !retailWizardBlocksHaveContent(draft.content_blocks)) return toast(`Add ${topic.toLowerCase()} content before continuing`);
    wizard.skipped.delete(topic); wizard.stepIndex += 1; renderRetailEditor();
  };
  document.querySelectorAll('[data-wizard-paragraph]').forEach(input => input.oninput = () => { ensureContentBlocks(draft)[+input.dataset.wizardParagraph].text = input.value; });
  document.querySelectorAll('[data-wizard-list-label]').forEach(input => input.oninput = () => { ensureContentBlocks(draft)[+input.dataset.wizardListLabel].label = input.value; });
  document.querySelectorAll('[data-wizard-list-item]').forEach(input => input.oninput = () => { const [blockIndex, itemIndex] = input.dataset.wizardListItem.split(':').map(Number); ensureContentBlocks(draft)[blockIndex].items[itemIndex] = input.value; });
  document.querySelectorAll('[data-wizard-list-add]').forEach(button => button.onclick = () => { ensureContentBlocks(draft)[+button.dataset.wizardListAdd].items.push(''); renderRetailEditor(); });
  document.querySelectorAll('[data-wizard-list-remove]').forEach(button => button.onclick = () => { const [blockIndex, itemIndex] = button.dataset.wizardListRemove.split(':').map(Number); ensureContentBlocks(draft)[blockIndex].items.splice(itemIndex, 1); renderRetailEditor(); });
  document.querySelectorAll('[data-wizard-block-add]').forEach(button => button.onclick = () => {
    const type = button.dataset.wizardBlockAdd; ensureContentBlocks(draft).push(type === 'paragraph' ? {type, text: ''} : {type, label: topic, items: ['']});
    renderRetailEditor();
  });
  document.querySelectorAll('[data-wizard-block-remove]').forEach(button => button.onclick = () => {
    const blocks = ensureContentBlocks(draft); blocks.splice(+button.dataset.wizardBlockRemove, 1); if (!blocks.length) blocks.push({type: 'paragraph', text: ''});
    renderRetailEditor();
  });
}

function renderRetailEditor() {
  const data = state.selected.data; $('#raw').value = JSON.stringify(data, null, 2); $('#raw').hidden = !state.raw; $('#visual').hidden = state.raw; if (state.raw) return;
  const reviewing = state.retailMode === 'review';
  $('#save').hidden = reviewing || !state.retailCategory;
  $('#save').textContent = state.me.approver ? 'Submit and approve' : 'Submit changes';
  $('#save').classList.toggle('submit-approve', !!state.me.approver);
  $('#approve').hidden = !(reviewing && state.me.approver && state.selected.pending && state.retailCategory);
  $('#approve').textContent = 'Approve changes';
  $('#deleteRetailPdf').hidden = reviewing || !state.me.approver || state.selected.new;
  const retailModeSwitch = reviewing ? `<div class="workflow-mode-switch" role="group" aria-label="Retail product task"><button class="secondary" data-retail-mode="edit">← Back to editing</button></div>` : '';
  const retailModeSummary = reviewing
    ? `<div class="validation-summary"><div><strong>Review retail product</strong><span>Check the submitted sections and tables, then select Approve changes.</span></div></div>`
    : `<div class="edit-summary"><strong>Edit content</strong><span>${state.me.approver ? 'Add or update product information, then select Submit and approve.' : 'Add or update product information, then submit the changes for approval.'}</span></div>`;
  state.retailSection = Math.min(state.retailSection, Math.max(0, data.sections.length - 1));
  const section = data.sections[state.retailSection];
  const pendingChanges = state.selected.changes || {document:[], sections:[]};
  const changedByIndex = new Map(pendingChanges.sections.filter(change => Number.isInteger(change.index)).map(change => [change.index, change]));
  const currentChange = changedByIndex.get(state.retailSection);
  const categories = retailCategories(data);
  if (!reviewing && !state.retailCategory && !state.retailWizard && !state.retailWizardDismissed && data.sections.length === 0) startRetailContentWizard(data, categories);
  if (!reviewing && !state.retailCategory && state.retailWizard) {
    $('#save').hidden = true; $('#approve').hidden = true;
    $('#visual').innerHTML = `<section class="retail-category-home">${renderRetailWizard(data)}</section>`;
    wireRetailWizard(data);
    return;
  }
  const categoryReviewCounts = new Map();
  pendingChanges.sections.forEach(change => {
    if (!Number.isInteger(change.index) || !data.sections[change.index]) return;
    const categoryName = retailCategoryFor(data.sections[change.index], data, change.index);
    categoryReviewCounts.set(categoryName, (categoryReviewCounts.get(categoryName) || 0) + 1);
  });
  const categoryChoices = [...categories].map(([name, indexes]) => {
    const reviewCount = categoryReviewCounts.get(name) || 0;
    const renamable = indexes.some(index => { const p = retailPath(data.sections[index], data, index); return retailCategoryBoundary(p) < p.length - 1; });
    const renaming = renamable && state.retailRenamingCategory === name;
    const renameField = renaming ? `<div class="retail-category-rename"><input id="retailCategoryRenameInput" value="${esc(name)}" aria-label="Rename content title"><button class="secondary" data-confirm-category-rename="${esc(name)}">Save</button><button class="secondary" data-cancel-category-rename>Cancel</button></div>` : '';
    const renameTrigger = renamable && !reviewing && !renaming ? `<button type="button" class="retail-category-rename-trigger" data-retail-category-label="${esc(name)}" title="Rename this content" aria-label="Rename ${esc(name)}">✎</button>` : '';
    return `<div class="retail-category-item">${renameField}<div class="retail-category-row-wrap"><button class="retail-category-row" data-retail-category="${esc(name)}"><span class="retail-category-mark">${esc(name.slice(0, 1).toUpperCase())}</span><span class="retail-category-name"><strong>${esc(name)}</strong><small>${esc(retailCategorySummary(data, name, indexes))}${reviewCount ? ` · ${reviewCount} awaiting review` : ''}</small></span><b>›</b></button>${renameTrigger}</div></div>`;
  }).join('');
  const categoryIndexes = state.retailCategory ? categories.get(state.retailCategory) || [] : [];
  const showSectionSearch = categoryIndexes.length > 6;
  if (!showSectionSearch) state.retailSearch = '';
  const sectionSearchBar = showSectionSearch ? `<label class="search retail-search"><span>⌕</span><input id="retailSectionSearch" placeholder="Find a section" value="${esc(state.retailSearch)}"></label>` : '';
  const path = section ? retailPath(section, data, state.retailSection) : [];
  const contentEditor = section ? renderRetailContent(section, reviewing, path.at(-1)) : '';
  const changeNotice = currentChange ? `<div class="approval-location"><strong>Approval needed in this section</strong><span>${currentChange.changes.map(esc).join(' · ')}</span></div>` : '';
  const subsectionControls = reviewing ? '' : state.retailAddingSubsection
    ? `<div class="retail-subsection-create"><label>Sub-section title<input id="retailSubsectionName" placeholder="For example: Platinum Credit Card"></label><div><button id="confirmAddRetailSubsection">Add</button><button id="cancelAddRetailSubsection" class="secondary">Cancel</button></div></div>`
    : `<button type="button" id="startAddRetailSubsection" class="retail-inline-link">+ Add a titled entry (e.g. Platinum, Gold)</button>`;
  const groupPath = Array.isArray(state.retailGroupPath) ? state.retailGroupPath : null;
  const groupEditorInner = groupPath ? `<article class="retail-business-editor"><div class="retail-editor-heading"><div><p class="eyebrow">PARENT TOPIC</p><div class="retail-breadcrumb">${groupPath.slice(0, -1).map(esc).join(' <span>›</span> ')}</div><h3>${esc(groupPath.at(-1))}</h3></div></div><div class="retail-section-name-edit"><label>Topic name<input id="retailGroupTitle" value="${esc(groupPath.at(-1))}" ${reviewing ? 'readonly' : ''}></label>${reviewing ? '' : '<button id="confirmRetailGroupRename" class="secondary">Save name</button>'}</div><div class="retail-parent-overview"><div><strong>Overview</strong><p>This topic does not have overview content. Its sub-sections can still be opened and edited from the left.</p></div>${reviewing ? '' : '<button id="addRetailGroupOverview" class="secondary">+ Add optional overview</button>'}</div></article>` : '';
  const groupEditorHtml = groupEditorInner ? `<div class="retail-outline-editor">${groupEditorInner}</div>` : '';
  const sectionEditorInner = !groupPath && section ? `<article class="retail-business-editor ${reviewing ? 'retail-review-mode' : ''} ${currentChange ? 'pending-change' : ''}">${changeNotice}<div class="retail-editor-heading"><div><div class="retail-breadcrumb">${path.slice(0, -1).map(esc).join(' <span>›</span> ')}</div>${reviewing ? `<h3>${esc(path.at(-1))}</h3>` : `<h3 class="retail-section-title-live"><input id="businessSectionTitle" value="${esc(path.at(-1))}" aria-label="Section name"><span class="retail-title-hint">Press Enter to save · Esc to cancel</span></h3>`}</div><div class="retail-editor-actions">${reviewing ? '' : '<button id="removeRetailSection" class="danger">Delete section</button>'}</div></div>${subsectionControls}${contentEditor}</article>` : '';
  const sectionEditorHtml = sectionEditorInner ? `<div class="retail-outline-editor">${sectionEditorInner}</div>` : '';
  const emptyCategoryNotice = state.retailCategory && categoryIndexes.length === 0 ? '<div class="empty-sections"><h3>No extracted sections</h3><p>Add a section to begin.</p></div>' : '';
  const sectionList = state.retailCategory ? retailTree(data, state.retailCategory, changedByIndex, state.retailSearch, sectionEditorHtml, groupEditorHtml) : '';
  const reviewSummary = state.selected.pending ? `<div class="pending-review-summary"><div><strong>${pendingChanges.sections.length + pendingChanges.document.length} change area${pendingChanges.sections.length + pendingChanges.document.length === 1 ? '' : 's'} need approval</strong><span>Sections marked Review contain differences from the currently approved data.</span></div>${pendingChanges.sections.length ? `<button id="firstRetailChange" class="secondary">Go to first change</button>` : ''}</div>` : '';
  const addContentButton = reviewing || state.retailAddingContent ? '' : '<button id="startAddRetailContent" class="secondary">+ Add content</button>';
  const existingCategoryNames = new Set([...categories.keys()].map(retailNorm));
  const suggestionTags = RETAIL_CONTENT_TAG_SUGGESTIONS.filter(tag => !existingCategoryNames.has(retailNorm(tag)));
  const addContentCard = !reviewing && state.retailAddingContent
    ? `<div class="retail-add-content-card"><label>Content title<input id="retailContentName" placeholder="For example: Eligibility" aria-label="Content title"></label>${suggestionTags.length ? `<div class="retail-tag-suggestions">${suggestionTags.map(tag => `<button type="button" class="retail-tag-chip" data-content-tag="${esc(tag)}">${esc(tag)}</button>`).join('')}</div>` : ''}<div class="retail-add-content-actions"><button id="confirmAddRetailContent">Add</button><button id="cancelAddRetailContent" class="secondary">Cancel</button></div></div>`
    : '';
  const showGlobalSearch = data.sections.length > 6;
  if (!showGlobalSearch) state.retailGlobalSearch = '';
  const globalQuery = state.retailGlobalSearch || '';
  const globalMatches = globalQuery.trim() ? retailGlobalMatches(data, categories, globalQuery) : null;
  const globalSearchBar = showGlobalSearch ? `<label class="search retail-search"><span>⌕</span><input id="retailGlobalSearch" placeholder="Search all sections" value="${esc(globalQuery)}"></label>` : '';
  const categoryListBody = categoryChoices + addContentCard || '<p class="no-results">No content yet. Select Add content to create the first content group.</p>';
  const globalResultsBody = globalMatches
    ? (globalMatches.length
        ? globalMatches.map(m => `<div class="retail-outline-item"><button class="retail-search-result-row" data-retail-jump="${m.index}"><strong>${esc(m.title)}</strong><small>${esc(m.categoryName)}</small></button></div>`).join('')
        : `<p class="no-results">No sections match "${esc(globalQuery.trim())}".</p>`)
    : `<div class="retail-category-list">${categoryListBody}</div>`;
  const categoryHome = `<section class="retail-category-home"><div class="retail-content-heading"><div><p class="eyebrow">DOCUMENT CONTENT</p><h2>Content</h2></div>${addContentButton}</div>${globalSearchBar}<div id="retailCategoryBody">${globalResultsBody}</div></section>`;
  const categoryNames = [...categories.keys()];
  const categorySwitcher = !reviewing && categoryNames.length > 1
    ? `<select id="retailCategorySwitch" aria-label="Switch content group">${categoryNames.map(name => `<option value="${esc(name)}" ${name === state.retailCategory ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select>`
    : `<strong>${esc(state.retailCategory || '')}</strong>`;
  const categoryHeader = `<div class="retail-list-header"><button id="backToRetailCategories" class="icon-btn" title="All content">←</button><div class="retail-list-header-title"><small>Content</small>${categorySwitcher}</div><span>${state.retailCategory ? retailCategoryCount(data, state.retailCategory, categories.get(state.retailCategory) || []) : data.sections.length}</span></div>`;
  const addSectionControls = reviewing ? '' : state.retailAddingSection
    ? `<div class="retail-add-content retail-add-section"><input id="retailSectionName" placeholder="${state.retailAddingSection === 'table' ? 'Table name' : 'Section title'}" aria-label="${state.retailAddingSection === 'table' ? 'Table name' : 'Section title'}"><button id="confirmAddRetailSection">Add</button><button id="cancelAddRetailSection" class="secondary">Cancel</button></div>`
    : `<div class="retail-section-create-wrap"><button id="startAddRetailSection" class="secondary">+ Add section</button><button id="startAddRetailTable" class="secondary">+ Add table</button></div>`;
  const documentName = state.retailRenaming && !reviewing ? `<div class="retail-document-bar retail-rename-bar"><label>Document name<input id="retailTitle" value="${esc(data.title)}" autofocus></label><button id="confirmRetailRename">Save name</button><button id="cancelRetailRename" class="secondary">Cancel</button></div>` : `<div class="retail-document-title"><div><span>Document name</span><strong>${esc(data.title)}</strong></div>${reviewing ? '' : '<button id="renameRetailDocument" class="secondary">Rename</button>'}</div>`;
  $('#visual').innerHTML = `${retailModeSwitch}${retailModeSummary}${reviewSummary}${documentName}${state.retailCategory ? `<div class="retail-outline-panel">${categoryHeader}${addSectionControls}${sectionSearchBar}<div id="retailSectionItems">${sectionList}${emptyCategoryNotice}</div></div>` : categoryHome}`;
  document.querySelectorAll('[data-retail-mode]').forEach(button => button.onclick = () => { if (button.disabled) return; state.retailMode = button.dataset.retailMode; renderRetailEditor(); });
  if ($('#renameRetailDocument')) $('#renameRetailDocument').onclick = () => { state.retailRenaming = true; renderRetailEditor(); };
  if ($('#cancelRetailRename')) $('#cancelRetailRename').onclick = () => { state.retailRenaming = false; renderRetailEditor(); };
  if ($('#confirmRetailRename')) $('#confirmRetailRename').onclick = () => { const updated = String($('#retailTitle').value || '').trim(); if (!updated) return toast('Add a document name'); const previous = data.title; data.title = updated; data.sections.forEach((item, index) => { const itemPath = retailPath(item, data, index); if (itemPath[0]?.toLowerCase() === String(previous).toLowerCase()) itemPath[0] = updated; item.path = itemPath; item.section_title = itemPath.join(' > '); }); state.retailRenaming = false; $('#docTitle').textContent = updated; setDirty(true); renderRetailEditor(); };
  const wireRetailOpenButtons = scope => {
    scope.querySelectorAll('[data-retail-open]').forEach(button => button.onclick = event => { event.preventDefault(); event.stopPropagation(); const idx = +button.dataset.retailOpen; const alreadyOpen = !Array.isArray(state.retailGroupPath) && state.retailSection === idx; state.retailGroupPath = null; state.retailSection = alreadyOpen ? -1 : idx; state.retailAddingSubsection = false; renderRetailEditor(); });
    scope.querySelectorAll('[data-retail-group]').forEach(button => button.onclick = event => { event.preventDefault(); event.stopPropagation(); const clickedPath = JSON.parse(decodeURIComponent(button.dataset.retailGroup)); const alreadyOpen = Array.isArray(state.retailGroupPath) && state.retailGroupPath.length === clickedPath.length && clickedPath.every((part, i) => retailNorm(part) === retailNorm(state.retailGroupPath[i])); state.retailGroupPath = alreadyOpen ? null : clickedPath; state.retailSection = -1; state.retailAddingSubsection = false; renderRetailEditor(); });
  };
  document.querySelectorAll('[data-retail-category]').forEach(button => button.onclick = () => { state.retailCategory = button.dataset.retailCategory; state.retailSection = categories.get(state.retailCategory)[0]; state.retailAddingSection = false; state.retailAddingSubsection = false; state.retailGroupPath = null; state.retailSearch = ''; state.retailGlobalSearch = ''; state.retailRenamingCategory = null; renderRetailEditor(); });
  document.querySelectorAll('[data-retail-jump]').forEach(button => button.onclick = () => { const jumpIndex = +button.dataset.retailJump; state.retailCategory = retailCategoryFor(data.sections[jumpIndex], data, jumpIndex); state.retailSection = jumpIndex; state.retailGroupPath = null; state.retailAddingSection = false; state.retailAddingSubsection = false; state.retailSearch = ''; state.retailGlobalSearch = ''; state.retailRenamingCategory = null; renderRetailEditor(); });
  if ($('#retailCategorySwitch')) $('#retailCategorySwitch').onchange = event => { state.retailCategory = event.target.value; state.retailSection = (categories.get(state.retailCategory) || [])[0] ?? -1; state.retailAddingSection = false; state.retailAddingSubsection = false; state.retailGroupPath = null; state.retailSearch = ''; state.retailRenamingCategory = null; renderRetailEditor(); };
  if ($('#retailGlobalSearch')) $('#retailGlobalSearch').oninput = event => { const cursorPos = event.target.selectionStart; state.retailGlobalSearch = event.target.value; renderRetailEditor(); const freshInput = $('#retailGlobalSearch'); if (freshInput) { freshInput.focus(); freshInput.setSelectionRange(cursorPos, cursorPos); } };
  if ($('#backToRetailCategories')) $('#backToRetailCategories').onclick = () => { state.retailCategory = null; state.retailAddingSection = false; state.retailAddingSubsection = false; state.retailGroupPath = null; state.retailSearch = ''; state.retailRenamingCategory = null; renderRetailEditor(); };
  document.querySelectorAll('[data-retail-category-label]').forEach(label => label.onclick = event => {
    event.preventDefault(); event.stopPropagation();
    state.retailRenamingCategory = label.dataset.retailCategoryLabel; renderRetailEditor();
    $('#retailCategoryRenameInput')?.focus(); $('#retailCategoryRenameInput')?.select();
  });
  document.querySelectorAll('[data-cancel-category-rename]').forEach(button => button.onclick = event => { event.preventDefault(); event.stopPropagation(); state.retailRenamingCategory = null; renderRetailEditor(); });
  document.querySelectorAll('[data-confirm-category-rename]').forEach(button => button.onclick = event => {
    event.preventDefault(); event.stopPropagation();
    const originalName = button.dataset.confirmCategoryRename;
    const updated = String($('#retailCategoryRenameInput')?.value || '').trim();
    if (!updated) return toast('Add a content title');
    if (retailNorm(updated) === retailNorm(originalName)) { state.retailRenamingCategory = null; return renderRetailEditor(); }
    if ([...categories.keys()].some(category => retailNorm(category) === retailNorm(updated))) return toast('A content group with this title already exists');
    (categories.get(originalName) || []).forEach(index => {
      const item = data.sections[index];
      const itemPath = retailPath(item, data, index);
      const boundary = retailCategoryBoundary(itemPath);
      if (boundary < itemPath.length - 1) { itemPath[boundary] = updated; item.path = itemPath; item.section_title = itemPath.join(' > '); }
    });
    if (state.retailCategory === originalName) state.retailCategory = updated;
    state.retailRenamingCategory = null; delete data.structure; setDirty(true); renderRetailEditor(); toast('Content renamed');
  });
  if ($('#retailCategoryRenameInput')) $('#retailCategoryRenameInput').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); $('[data-confirm-category-rename]')?.click(); } else if (event.key === 'Escape') { state.retailRenamingCategory = null; renderRetailEditor(); } };
  if ($('#confirmRetailGroupRename')) $('#confirmRetailGroupRename').onclick = () => {
    const updated = String($('#retailGroupTitle').value || '').trim(); if (!updated) return toast('Add a topic name');
    const original = [...groupPath]; if (retailNorm(updated) === retailNorm(original.at(-1))) return toast('Topic name is unchanged');
    data.sections.forEach((item, index) => { const itemPath = retailPath(item, data, index); if (itemPath.length >= original.length && original.every((part, i) => retailNorm(itemPath[i]) === retailNorm(part))) { item.path = [...original.slice(0, -1), updated, ...itemPath.slice(original.length)]; item.section_title = item.path.join(' > '); } });
    state.retailGroupPath = [...original.slice(0, -1), updated]; delete data.structure; setDirty(true); renderRetailEditor(); toast('Topic renamed');
  };
  if ($('#addRetailGroupOverview')) $('#addRetailGroupOverview').onclick = () => {
    const name = groupPath.at(-1); const overview = {topic:data.title, section:name, variant:'content', section_title:groupPath.join(' > '), path:[...groupPath], display_title:name, language:'en', images:[], content_blocks:[{type:'paragraph', text:''}]};
    syncRetailContent(overview); data.sections.push(overview); state.retailGroupPath = null; state.retailSection = data.sections.length - 1; setDirty(true); renderRetailEditor();
  };
  if ($('#startAddRetailContent')) $('#startAddRetailContent').onclick = () => {
    if (startRetailContentWizard(data, categories)) { renderRetailEditor(); return; }
    state.retailAddingContent = true; renderRetailEditor(); $('#retailContentName')?.focus();
  };
  if ($('#cancelAddRetailContent')) $('#cancelAddRetailContent').onclick = () => { state.retailAddingContent = false; renderRetailEditor(); };
  document.querySelectorAll('[data-content-tag]').forEach(button => button.onclick = () => { const input = $('#retailContentName'); if (input) { input.value = button.dataset.contentTag; input.focus(); } });
  if ($('#retailContentName')) $('#retailContentName').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); $('#confirmAddRetailContent')?.click(); } };
  if ($('#confirmAddRetailContent')) $('#confirmAddRetailContent').onclick = () => {
    const name = String($('#retailContentName').value || '').trim();
    if (!name) return toast('Add a content title');
    if ([...categories.keys()].some(category => retailNorm(category) === retailNorm(name))) return toast('A content group with this title already exists');
    const newPath = [data.title, name, 'Description'];
    const section = {topic:data.title, section:'Description', variant:'description', section_title:newPath.join(' > '), path:newPath, display_title:'Description', language:'en', images:[], content_blocks:[{type:'paragraph', text:`Add the ${name.toLowerCase()} description here.`}]};
    syncRetailContent(section); data.sections.push(section); state.retailSection = data.sections.length - 1; state.retailCategory = name; state.retailSearch = ''; state.retailAddingContent = false; setDirty(true); renderRetailEditor();
  };
  if ($('#startAddRetailSection')) $('#startAddRetailSection').onclick = () => { state.retailAddingSection = 'section'; renderRetailEditor(); $('#retailSectionName')?.focus(); };
  if ($('#startAddRetailTable')) $('#startAddRetailTable').onclick = () => { state.retailAddingSection = 'table'; renderRetailEditor(); $('#retailSectionName')?.focus(); };
  if ($('#cancelAddRetailSection')) $('#cancelAddRetailSection').onclick = () => { state.retailAddingSection = false; renderRetailEditor(); };
  if ($('#confirmAddRetailSection')) $('#confirmAddRetailSection').onclick = () => {
    const name = String($('#retailSectionName').value || '').trim();
    const creatingTable = state.retailAddingSection === 'table';
    if (!name) return toast(creatingTable ? 'Add a table name' : 'Add a section title');
    const existing = categoryIndexes.some(index => retailNorm(retailPath(data.sections[index], data, index).at(-1)) === retailNorm(name));
    if (existing) return toast('A section with this title already exists');
    const newPath = creatingTable ? [data.title, state.retailCategory, 'Tables', name] : [data.title, state.retailCategory, name];
    const section = {topic:data.title, section:name, variant:creatingTable ? 'table' : 'content', section_title:newPath.join(' > '), path:newPath, display_title:name, language:'en', images:[]};
    if (creatingTable) { section.table_rows = [['Column 1', 'Column 2'], ['', '']]; section.content = serializeTable(section.table_rows); }
    else {
      section.content_blocks = [{type:'paragraph', text:`Add the ${name.toLowerCase()} details here.`}];
      syncRetailContent(section);
    }
    data.sections.push(section); state.retailSection = data.sections.length - 1; state.retailAddingSection = false; state.retailSearch = ''; setDirty(true); renderRetailEditor();
  };
  if ($('#startAddRetailSubsection')) $('#startAddRetailSubsection').onclick = () => { state.retailAddingSubsection = true; renderRetailEditor(); $('#retailSubsectionName')?.focus(); };
  if ($('#cancelAddRetailSubsection')) $('#cancelAddRetailSubsection').onclick = () => { state.retailAddingSubsection = false; renderRetailEditor(); };
  if ($('#confirmAddRetailSubsection')) $('#confirmAddRetailSubsection').onclick = () => {
    const name = String($('#retailSubsectionName').value || '').trim(); if (!name) return toast('Add a sub-section title');
    const parentPath = retailPath(section, data, state.retailSection); const parentKey = parentPath.map(retailNorm).join('>');
    const duplicate = data.sections.some((item, index) => { const itemPath = retailPath(item, data, index); return itemPath.length === parentPath.length + 1 && itemPath.slice(0, -1).map(retailNorm).join('>') === parentKey && retailNorm(itemPath.at(-1)) === retailNorm(name); });
    if (duplicate) return toast('A sub-section with this title already exists');
    const newPath = [...parentPath, name]; const child = {topic:data.title, section:name, variant:'content', section_title:newPath.join(' > '), path:newPath, display_title:name, language:'en', images:[], content_blocks:[{type:'paragraph', text:`Add the ${name.toLowerCase()} details here.`}]};
    syncRetailContent(child); data.sections.push(child); state.retailSection = data.sections.length - 1; state.retailAddingSubsection = false; setDirty(true); renderRetailEditor();
  };
  if ($('#firstRetailChange')) $('#firstRetailChange').onclick = () => { const first = pendingChanges.sections.find(change => Number.isInteger(change.index)); if (first) { state.retailSection = first.index; state.retailCategory = retailCategoryFor(data.sections[first.index], data, first.index); state.retailSearch = ''; renderRetailEditor(); } };
  wireRetailOpenButtons(document);
  if ($('#retailSectionSearch')) $('#retailSectionSearch').oninput = event => {
    const cursorPos = event.target.selectionStart; state.retailSearch = event.target.value;
    if (state.retailSection >= 0 || groupPath) { renderRetailEditor(); const freshInput = $('#retailSectionSearch'); if (freshInput) { freshInput.focus(); freshInput.setSelectionRange(cursorPos, cursorPos); } return; }
    $('#retailSectionItems').innerHTML = retailTree(data, state.retailCategory, changedByIndex, state.retailSearch, sectionEditorHtml, groupEditorHtml) + emptyCategoryNotice;
    wireRetailOpenButtons($('#retailSectionItems'));
  };
  if (!state.retailCategory || !section) return;
  if (reviewing) {
    document.querySelectorAll('.business-table textarea').forEach(field => field.readOnly = true);
    document.querySelectorAll('.business-table button').forEach(button => button.hidden = true);
    return;
  }
  if ($('#businessSectionTitle')) {
    const commitSectionRename = () => {
      const updated = String($('#businessSectionTitle').value || '').trim(); if (!updated) return toast('Add a section name');
      const originalPath = [...path]; const originalName = originalPath.at(-1); if (retailNorm(updated) === retailNorm(originalName)) return;
      const parentKey = originalPath.slice(0, -1).map(retailNorm).join('>');
      const duplicate = data.sections.some((item, index) => index !== state.retailSection && (() => { const itemPath = retailPath(item, data, index); return itemPath.length === originalPath.length && itemPath.slice(0, -1).map(retailNorm).join('>') === parentKey && retailNorm(itemPath.at(-1)) === retailNorm(updated); })());
      if (duplicate) return toast('A section with this name already exists here');
      const updatedPath = [...originalPath.slice(0, -1), updated];
      data.sections.forEach((item, index) => {
        const itemPath = retailPath(item, data, index); const descendant = itemPath.length >= originalPath.length && originalPath.every((part, partIndex) => retailNorm(itemPath[partIndex]) === retailNorm(part));
        if (!descendant) return;
        item.path = [...updatedPath, ...itemPath.slice(originalPath.length)]; item.section_title = item.path.join(' > ');
        if (index === state.retailSection) { item.display_title = updated; item.section = updated; (item.content_blocks || []).forEach(block => { if (retailNorm(block.label) === retailNorm(originalName)) block.label = updated; }); syncRetailContent(item); }
      });
      if (originalPath.length === 2 && retailNorm(state.retailCategory) === retailNorm(originalName)) state.retailCategory = updated;
      delete data.structure; setDirty(true); renderRetailEditor(); toast('Section renamed');
    };
    $('#businessSectionTitle').onkeydown = event => {
      if (event.key === 'Enter') { event.preventDefault(); commitSectionRename(); }
      else if (event.key === 'Escape') { event.preventDefault(); event.target.value = path.at(-1); event.target.blur(); }
    };
  }
  document.querySelectorAll('[data-retail-paragraph]').forEach(input => input.oninput = () => { ensureContentBlocks(section)[+input.dataset.retailParagraph].text = input.value; syncRetailContent(section); setDirty(true); });
  document.querySelectorAll('[data-retail-list-label]').forEach(input => input.oninput = () => { ensureContentBlocks(section)[+input.dataset.retailListLabel].label = input.value; syncRetailContent(section); setDirty(true); });
  document.querySelectorAll('[data-retail-list-item]').forEach(input => input.oninput = () => { const [blockIndex, itemIndex] = input.dataset.retailListItem.split(':').map(Number); ensureContentBlocks(section)[blockIndex].items[itemIndex] = input.value; syncRetailContent(section); setDirty(true); });
  document.querySelectorAll('[data-retail-list-add]').forEach(button => button.onclick = () => { ensureContentBlocks(section)[+button.dataset.retailListAdd].items.push(''); syncRetailContent(section); setDirty(true); renderRetailEditor(); });
  document.querySelectorAll('[data-retail-list-remove]').forEach(button => button.onclick = () => { const [blockIndex, itemIndex] = button.dataset.retailListRemove.split(':').map(Number); ensureContentBlocks(section)[blockIndex].items.splice(itemIndex, 1); syncRetailContent(section); setDirty(true); renderRetailEditor(); });
  document.querySelectorAll('[data-retail-block-add]').forEach(button => button.onclick = () => {
    const type = button.dataset.retailBlockAdd; ensureContentBlocks(section).push(type === 'paragraph' ? {type, text:''} : {type, label:path.at(-1), items:['']});
    syncRetailContent(section); setDirty(true); renderRetailEditor();
  });
  document.querySelectorAll('[data-retail-block-remove]').forEach(button => button.onclick = () => {
    const blocks = ensureContentBlocks(section); blocks.splice(+button.dataset.retailBlockRemove, 1); if (!blocks.length) blocks.push({type:'paragraph', text:''});
    syncRetailContent(section); setDirty(true); renderRetailEditor();
  });
  wireTableEditor(section);
  $('#removeRetailSection').onclick = () => { if (!confirm(`Delete section ${state.retailSection + 1}?`)) return; data.sections.splice(state.retailSection, 1); state.retailCategory = null; state.retailSection = 0; setDirty(true); renderRetailEditor(); };
}

function shortSectionName(section, index) { const title = String(section.section_title || section.section || `Section ${index + 1}`); return title.split('>').pop().trim() || `Section ${index + 1}`; }
function setDirty(value) { state.dirty = value; $('#dirtyStatus').hidden = !value; }

function parseTable(content) {
  const rows = String(content || '').split(/\r?\n/).map(line => line.split('|').map(cell => cell.trim()));
  for (const row of rows) { if (row[0] === '') row.shift(); if (row[row.length - 1] === '') row.pop(); }
  return rows.filter(row => row.some(Boolean));
}
function serializeTable(rows) { return rows.map(row => row.map(cell => String(cell || '').trim()).join(' | ')).join('\n'); }
function tableRows(section) {
  const stored = section?.table_rows;
  const rows = Array.isArray(stored) && stored.length
    ? stored.map(row => Array.isArray(row) ? row.map(cell => String(cell ?? '')) : [])
    : parseTable(section?.content);
  if (!rows.length) rows.push(['']);
  const columns = Math.max(1, ...rows.map(row => row.length));
  rows.forEach(row => { while (row.length < columns) row.push(''); });
  return rows;
}
function updateTable(section, rows) {
  section.table_rows = rows.map(row => row.map(cell => String(cell ?? '')));
  section.content = serializeTable(section.table_rows);
  setDirty(true);
}
function renderEditableTable(section) {
  const rows = tableRows(section); const columns = rows[0].length;
  const body = rows.map((row, rowIndex) => `<tr>${row.map((cell, columnIndex) => `<${rowIndex === 0 ? 'th' : 'td'}><textarea data-table-cell="${rowIndex}" data-table-column="${columnIndex}">${esc(cell)}</textarea></${rowIndex === 0 ? 'th' : 'td'}>`).join('')}<td class="row-action"><button data-remove-table-row="${rowIndex}" class="danger" title="Delete row">×</button></td></tr>`).join('');
  return `<div class="business-table"><div class="business-table-head"><div><strong>Editable table</strong><span>Click any cell to change its value.</span></div><div><button id="addTableRow" class="secondary">+ Row</button><button id="addTableColumn" class="secondary">+ Column</button><button id="removeTableColumn" class="secondary" ${columns <= 1 ? 'disabled' : ''}>− Column</button></div></div><div class="table-scroll"><table><tbody>${body}</tbody></table></div></div>`;
}
function wireTableEditor(section) {
  if (!section || section.variant !== 'table' || !$('#addTableRow')) return;
  let rows = tableRows(section); let columns = rows[0].length;
  document.querySelectorAll('[data-table-cell]').forEach(cell => cell.oninput = () => { rows[+cell.dataset.tableCell][+cell.dataset.tableColumn] = cell.value; updateTable(section, rows); });
  $('#addTableRow').onclick = () => { rows.push(Array(columns).fill('')); updateTable(section, rows); renderRetailEditor(); };
  $('#addTableColumn').onclick = () => { rows.forEach(row => row.push('')); updateTable(section, rows); renderRetailEditor(); };
  $('#removeTableColumn').onclick = () => { if (columns <= 1) return; rows.forEach(row => row.pop()); updateTable(section, rows); renderRetailEditor(); };
  document.querySelectorAll('[data-remove-table-row]').forEach(button => button.onclick = () => { rows.splice(+button.dataset.removeTableRow, 1); if (!rows.length) rows.push(Array(columns).fill('')); updateTable(section, rows); renderRetailEditor(); });
}

function collectCurrent() {
  if (state.raw) return JSON.parse($('#raw').value);
  if (state.domain === 'retail') { if ($('#retailTitle')) state.selected.data.title = $('#retailTitle').value.trim(); return state.selected.data; }
  return collect();
}

function renderCurrent() { if (state.domain === 'retail') renderRetailEditor(); else renderEditor(); }

async function uploadStepImage(stepIndex, file) {
  if (!file) return;
  try {
    const form = new FormData(); form.append('file', file); form.append('workflow', state.selected.path);
    const result = await api('/api/data/steps/asset', {method: 'POST', body: form});
    const step = state.selected.data.steps[stepIndex];
    const images = String(step.image || '').split(',').map(value => value.trim()).filter(Boolean);
    if (!images.includes(result.filename)) images.push(result.filename);
    step.image = images.join(', '); invalidateReview(state.selected.data, `steps.${stepIndex}.image`); renderEditor(); toast('Image added. Submit changes for approval.');
  } catch (error) { showNotice(error.message, 'error'); }
}

async function submitCurrentChanges() {
  const selectedPath = state.selected.path;
  const data = collectCurrent(); if (Array.isArray(data.steps) && (!data.title || data.steps.some(step => !String(step.text || '').trim()))) throw new Error('A title and text for every entry are required.');
  if (Array.isArray(data.sections) && data.sections.some(section => !String(section.content || '').trim())) throw new Error('Every retail section needs text.');
  const url = state.domain === 'retail' ? `/api/data/retail/extracted?path=${encodeURIComponent(selectedPath)}` : `/api/data/steps/file?path=${encodeURIComponent(selectedPath)}`;
  const result = await api(url, {method: 'PUT', body: JSON.stringify({data})});
  return {selectedPath, result};
}
async function save() {
  try {
    $('#save').disabled = true;
    const submittedDomain = state.domain;
    const {selectedPath, result} = await submitCurrentChanges();
    setDirty(false);
    if (state.me.approver) {
      const approveResult = await api(`/api/data/${submittedDomain}/approve`, {method: 'POST', body: JSON.stringify({path: selectedPath})});
      await selectDomain(submittedDomain); await openFile(selectedPath);
      const synced = approveResult.milvus?.status === 'synced';
      showNotice(approveResult.message, state.me.admin && !synced ? 'warning' : 'success');
      toast(synced && state.me.admin ? 'Data submitted, approved, and imported' : 'Data submitted and approved');
      return;
    }
    await selectDomain(submittedDomain); await openFile(selectedPath);
    if (submittedDomain === 'steps' && selectedPath.toLowerCase().endsWith('.json')) { state.workflowMode = 'review'; renderEditor(); }
    if (submittedDomain === 'retail') { state.retailMode = 'review'; renderRetailEditor(); }
    showNotice('Changes submitted. An approver will review and approve them.', 'warning');
    toast('Changes submitted for approval');
  } catch (error) { showNotice(error.message, 'error'); } finally { $('#save').disabled = false; }
}
$('#search').oninput = renderFiles;
$('#historySearch').oninput = () => { state.historySearch = $('#historySearch').value; renderHistory(); };
$('#newMainTopicForm').onsubmit = event => {
  event.preventDefault();
  const topic = String($('#newMainTopicName').value || '').replace(/[<>:"/\\|?*]+/g, ' ').trim();
  if (!topic) return;
  state.activeTopic = topic; state.topicBrowsePath = ''; state.newWorkflowDraft = {topic, parent:'', title: '', type:'workflow'};
  $('#newMainTopicName').value = ''; $('#newMainTopicForm').hidden = true; $('#newWorkflow').hidden = false;
  rememberTopic('steps', topic); forgetSelection('steps'); renderFiles();
};
$('#cancelNewMainTopic').onclick = () => { $('#newMainTopicName').value = ''; $('#newMainTopicForm').hidden = true; $('#newWorkflow').hidden = false; };
$('#exportSteps').onclick = async () => {
  const button = $('#exportSteps'); const overlay = $('#exportingOverlay');
  button.disabled = true; button.textContent = 'Preparing PDF…'; overlay.hidden = false;
  try {
    const response = await fetch('/api/data/steps/export');
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || 'Could not prepare the PDF export'); }
    const blob = await response.blob();
    const filename = /filename="?([^";]+)"?/i.exec(response.headers.get('content-disposition') || '')?.[1] || 'capital-data-studio-steps-and-workflows.pdf';
    const url = URL.createObjectURL(blob); const download = document.createElement('a');
    download.href = url; download.download = filename; document.body.append(download); download.click(); download.remove(); URL.revokeObjectURL(url);
    toast('PDF export downloaded');
  } catch (error) { showNotice(error.message, 'error'); }
  finally { overlay.hidden = true; button.disabled = false; button.textContent = '↓ Export all topics (PDF)'; }
};
$('#exportRetailProduct').onclick = () => { if (state.domain === 'retail' && state.selected?.path) downloadRetailExport(state.selected.path, $('#exportRetailProduct')); };
$('#backToRetailCollection').onclick = () => { if (state.retailCollection) renderRetailCollection(state.retailCollection); };
$('#newRetailSubproduct').onclick = () => {
  if (state.domain !== 'retail' || !state.selected?.path) return;
  const parts = state.selected.path.split('/');
  const parent = parts.length > 1 ? parts[0] : String(state.selected.title || state.selected.name || parts[0]).replace(/\.[^.]+$/, '').trim();
  const kind = retailNorm(parent) === 'loans' ? 'loan' : 'product';
  openRetailProductCreate(parent, kind);
};
$('#logout').onclick = async () => { await api('/api/auth/logout', {method: 'POST'}); sessionStorage.removeItem(ACTIVE_SESSION_KEY); location = '/login.html'; };
$('#backToTopic').onclick = () => { state.selected = null; forgetSelection('steps'); $('#editor').hidden = true; renderFiles(); renderTopicExplorer(); };
$('#save').onclick = save;
$('#approve').onclick = async () => {
  const button = $('#approve'), overlay = $('#approvalOverlay'), originalLabel = button.textContent;
  try {
    const selectedPath = state.selected.path;
    button.disabled = true; button.textContent = 'Approving…'; overlay.hidden = false;
    if (state.domain === 'steps' && Array.isArray(state.selected.data?.steps)) { state.selected.data.validation = state.selected.data.validation && typeof state.selected.data.validation === 'object' ? state.selected.data.validation : {}; state.selected.data.validation.reviewed_fields = workflowReviewStatus(state.selected.data).required; setDirty(true); }
    const referenceCount = referenceItemCount(state.selected.data); if (referenceCount !== null) saveReferenceReviewed(new Set(referenceReviewStatus(referenceCount).required));
    if (state.dirty || !state.selected.pending) { const staged = await submitCurrentChanges(); state.selected.pending = staged.result.pending; setDirty(false); }
    const result = await api(`/api/data/${state.domain}/approve`, {method: 'POST', body: JSON.stringify({path: selectedPath})});
    await selectDomain(state.domain); await openFile(selectedPath); setDirty(false);
    const synced = result.milvus?.status === 'synced'; showNotice(result.message, state.me.admin && !synced ? 'warning' : 'success'); toast(synced && state.me.admin ? 'Data approved and imported' : 'Data approved');
  } catch (error) { showNotice(error.message, 'error'); }
  finally { overlay.hidden = true; button.disabled = false; button.textContent = originalLabel; }
};
$('#deleteWorkflow').onclick = async () => {
  const title = state.selected.title || state.selected.name;
  const documentLabel = referenceDocumentLabel(state.selected.data);
  if (!confirm(`Delete ${documentLabel} “${title}”? This will also remove it from the AI knowledge base.`)) return;
  try { const result = await api(`/api/data/steps/workflow?path=${encodeURIComponent(state.selected.path)}`, {method: 'DELETE'}); await selectDomain('steps'); toast(result.message); } catch (error) { showNotice(error.message, 'error'); }
};
$('#deleteRetailPdf').onclick = async () => {
  const title = state.selected.title || state.selected.name;
  if (!confirm(`Delete “${title}”? The product, approved data, and AI knowledge base entry will be removed.`)) return;
  try { const result = await api(`/api/data/retail/pdf?path=${encodeURIComponent(state.selected.path)}`, {method:'DELETE'}); await selectDomain('retail'); toast(result.message); } catch (error) { showNotice(error.message, 'error'); }
};
function chooseRetailExistingRole() {
  const dialog = $('#retailCollectionChoice');
  return new Promise(resolve => {
    dialog.returnValue = '';
    dialog.querySelectorAll('[data-existing-role]').forEach(button => button.onclick = () => dialog.close(button.dataset.existingRole));
    dialog.onclose = () => resolve(dialog.returnValue || null);
    dialog.showModal();
  });
}

function setRetailJourneyFocus(active) {
  const aside = document.querySelector('main>aside'); if (aside) aside.hidden = active;
  $('main').classList.toggle('history-mode', active);
}

function renderRetailConfigure(path, parent, title, existingRole = '') {
  setRetailJourneyFocus(true);
  state.selected = null; forgetSelection('retail'); $('#editor').hidden = true; $('#empty').hidden = false;
  $('#empty').className = 'empty retail-configure-page';
  const parentLabel = state.retailCollectionLabels[parent] || parent;
  const kind = retailNorm(parent) === 'loans' ? 'loan' : 'product';
  const icon = retailTitleIconSvg(title);
  const steps = [
    {title: 'Choose a tag', desc: `Select a label that categorises this ${kind} product.`},
    {title: 'Description', desc: `Write a short description of the ${kind} product.`},
    {title: 'Eligibility criteria', desc: `Define who is eligible for this ${kind}.`},
    {title: 'Required documents', desc: 'List the documents applicants must provide.'},
    {title: 'Terms & conditions', desc: `Set out the terms and conditions for this ${kind}.`},
    {title: 'Review & publish', desc: `Review your input and publish the ${kind} product.`}
  ];
  $('#empty').innerHTML = `<div class="retail-configure-shell"><button class="retail-configure-back" id="retailConfigureBack">← Back</button><p class="retail-crumb">${esc(state.domain)}${parentLabel ? ` / ${esc(parentLabel)}` : ''} /</p><div class="retail-configure-title"><span class="retail-create-icon">${icon}</span><h2>${esc(title)}</h2></div><div class="retail-configure-card"><h3>Configure this ${esc(kind)} product</h3><p class="muted">Complete all steps to publish <strong>${esc(title)}</strong> to the knowledge base. Everything you add here will appear together under the Overview of this product.</p><ol class="retail-configure-steps">${steps.map((step, index) => `<li><b>${index + 1}</b><span><strong>${esc(step.title)}</strong><small>${esc(step.desc)}</small></span></li>`).join('')}</ol><button id="retailStartConfigure" class="retail-configure-start">Start configuration →</button></div></div>`;
  $('#retailConfigureBack').onclick = () => { if (parent) return renderRetailCollection(parent); setRetailJourneyFocus(false); selectDomain('retail'); };
  $('#retailStartConfigure').onclick = () => startRetailConfiguration(path, parent, title, existingRole);
}
const RETAIL_WIZARD_STEPS = [
  {title: 'Choose a tag'},
  {title: 'Description'},
  {title: 'Eligibility criteria'},
  {title: 'Required documents'},
  {title: 'Terms & conditions'},
  {title: 'Review & publish'}
];
const RETAIL_TAG_OPTIONS = ['Personal', 'Business', 'Mortgage', 'Auto', 'Education', 'Healthcare', 'Salary-backed'];
const RETAIL_DESCRIPTION_FORMATS = [
  {key: 'paragraph', label: 'Paragraph', icon: '¶', hint: 'Write a few sentences describing the product.', placeholder: 'A flexible loan designed to help customers purchase new or used vehicles, with competitive rates and manageable repayment terms.'},
  {key: 'bullets', label: 'Bullet points', icon: '•', hint: 'Add each key feature or benefit as its own point.', itemPlaceholder: 'e.g. Competitive interest rates'},
  {key: 'numbered', label: 'Numbered list', icon: '1.', hint: 'Add each point in the order it should appear.', itemPlaceholder: 'e.g. Competitive interest rates'},
  {key: 'one-liner', label: 'One-liner', icon: '–', hint: 'Sum up this product in a single sentence.', placeholder: 'A fast, flexible loan for buying new or used vehicles.'}
];
function retailDescriptionFormat(key) { return RETAIL_DESCRIPTION_FORMATS.find(f => f.key === key) || RETAIL_DESCRIPTION_FORMATS[0]; }
function retailDescriptionIsList(format) { return format === 'bullets' || format === 'numbered'; }
function retailWizardDescriptionText(wizard) {
  const desc = wizard.data.description || {};
  if (retailDescriptionIsList(desc.format)) {
    const items = (desc.items || []).map(item => String(item || '').trim()).filter(Boolean);
    if (desc.format === 'numbered') return items.map((item, index) => `${index + 1}. ${item}`).join('\n');
    return items.map(item => `• ${item}`).join('\n');
  }
  return String(desc.text || '').trim();
}

function startRetailConfiguration(path, parent, title, existingRole = '') {
  const kind = retailNorm(parent) === 'loans' ? 'loan' : 'product';
  state.retailCreateWizard = {path, parent, title, kind, existingRole, step: 0, data: {tag: '', description: {format: 'bullets', text: '', items: ['']}, eligibility: '', requiredDocuments: '', terms: [{text: ''}], termJoins: []}};
  renderRetailCreateWizardStep();
}

function retailWizardTermsLines(wizard) {
  const lines = [];
  (wizard.data.terms || []).forEach((term, index) => {
    const text = String(term.text || '').trim(); if (!text) return;
    if (index > 0) lines.push(wizard.data.termJoins?.[index - 1] || 'AND');
    lines.push(text);
  });
  return lines;
}

function retailWizardStepValid(wizard) {
  if (wizard.step === 0) return Boolean(String(wizard.data.tag || '').trim());
  if (wizard.step === 1) {
    const desc = wizard.data.description || {};
    if (retailDescriptionIsList(desc.format)) return (desc.items || []).length > 0 && desc.items.every(item => Boolean(String(item || '').trim()));
    return Boolean(String(desc.text || '').trim());
  }
  if (wizard.step === 2) return Boolean(String(wizard.data.eligibility || '').trim());
  if (wizard.step === 3) return Boolean(String(wizard.data.requiredDocuments || '').trim());
  if (wizard.step === 4) return (wizard.data.terms || []).every(term => Boolean(String(term.text || '').trim()));
  return true;
}

function retailWizardStepBody(wizard) {
  const step = RETAIL_WIZARD_STEPS[wizard.step];
  const total = RETAIL_WIZARD_STEPS.length;
  if (wizard.step === 0) {
    const tag = wizard.data.tag || '';
    return `<p class="eyebrow">STEP 1 OF ${total}</p><h1>Choose a tag</h1><p class="muted">Select a label that categorises this ${esc(wizard.kind)} product.</p><div class="retail-wizard-tags">${RETAIL_TAG_OPTIONS.map(option => `<button type="button" class="retail-wizard-tag ${tag === option ? 'active' : ''}" data-tag="${esc(option)}">${esc(option)}</button>`).join('')}</div><input id="retailWizardTagInput" class="retail-wizard-input" placeholder="Or type a custom tag" value="${esc(tag)}">`;
  }
  if (wizard.step === 1) {
    const desc = wizard.data.description || (wizard.data.description = {format: 'bullets', text: '', items: ['']});
    const active = retailDescriptionFormat(desc.format);
    const pillsHtml = `<div class="retail-wizard-format-pills">${RETAIL_DESCRIPTION_FORMATS.map(f => `<button type="button" class="retail-wizard-format-pill ${desc.format === f.key ? 'active' : ''}" data-format="${f.key}"><span>${f.icon}</span>${esc(f.label)}</button>`).join('')}</div><p class="retail-wizard-format-hint">${esc(active.hint)}</p>`;
    if (retailDescriptionIsList(desc.format)) {
      if (!Array.isArray(desc.items) || !desc.items.length) desc.items = [''];
      const marker = index => desc.format === 'numbered' ? `${index + 1}.` : '•';
      const rows = desc.items.map((item, index) => `<div class="retail-wizard-desc-item-row"><span class="retail-wizard-desc-item-marker">${marker(index)}</span><input type="text" class="retail-wizard-desc-item-input" data-index="${index}" placeholder="${esc(active.itemPlaceholder)}" value="${esc(item)}"></div>`).join('');
      const addLabel = desc.format === 'numbered' ? 'item' : 'point';
      return `<p class="eyebrow">STEP 2 OF ${total}</p><h1>Description</h1><p class="muted">Write a short description of the ${esc(wizard.kind)} product.</p>${pillsHtml}<div class="retail-wizard-desc-items">${rows}</div><button type="button" id="retailWizardAddDescItem" class="retail-wizard-add-condition">+ Add ${addLabel}</button>`;
    }
    return `<p class="eyebrow">STEP 2 OF ${total}</p><h1>Description</h1><p class="muted">Write a short description of the ${esc(wizard.kind)} product.</p>${pillsHtml}<textarea id="retailWizardDescriptionInput" class="retail-wizard-textarea" placeholder="${esc(active.placeholder)}">${esc(desc.text)}</textarea>`;
  }
  if (wizard.step === 2) {
    return `<p class="eyebrow">STEP 3 OF ${total}</p><h1>Eligibility criteria</h1><p class="muted">Define who is eligible for this ${esc(wizard.kind)}.</p><textarea id="retailWizardEligibilityInput" class="retail-wizard-textarea" placeholder="e.g. Must be 21+ years old, employed for at least 6 months...">${esc(wizard.data.eligibility || '')}</textarea>`;
  }
  if (wizard.step === 3) {
    return `<p class="eyebrow">STEP 4 OF ${total}</p><h1>Required documents</h1><p class="muted">List the documents applicants must provide.</p><textarea id="retailWizardDocumentsInput" class="retail-wizard-textarea" placeholder="e.g. Valid national ID, proof of income, proof of address...">${esc(wizard.data.requiredDocuments || '')}</textarea>`;
  }
  if (wizard.step === 4) {
    const terms = wizard.data.terms; const joins = wizard.data.termJoins;
    const conditionPlaceholders = ['e.g. Applicant must be at least 21 years old', 'e.g. Monthly income must exceed JOD 500'];
    const rows = terms.map((cond, index) => {
      const placeholder = conditionPlaceholders[index] || 'e.g. Additional condition';
      const row = `<div class="retail-wizard-condition-row"><span class="retail-wizard-condition-index">${index + 1}</span><input type="text" class="retail-wizard-condition-input" data-index="${index}" placeholder="${esc(placeholder)}" value="${esc(cond.text)}"></div>`;
      if (index < terms.length - 1) {
        const join = joins[index] || 'AND';
        return `${row}<div class="retail-wizard-condition-join"><button type="button" class="retail-wizard-join-toggle ${join === 'OR' ? 'or' : 'and'}" data-index="${index}">${join}</button><small>click to toggle</small></div>`;
      }
      return row;
    }).join('');
    return `<p class="eyebrow">STEP 5 OF ${total}</p><h1>Terms &amp; conditions</h1><p class="muted">Define each term or condition individually. Use AND / OR to chain them.</p><div class="retail-wizard-conditions">${rows}</div><button type="button" id="retailWizardAddCondition" class="retail-wizard-add-condition">+ Add condition</button>`;
  }
  if (wizard.step === total - 1) {
    const reviewItems = [];
    const tag = String(wizard.data.tag || '').trim();
    if (tag) reviewItems.push({label: 'TAG', value: tag});
    const descriptionText = retailWizardDescriptionText(wizard);
    if (descriptionText) {
      const format = retailDescriptionFormat(wizard.data.description.format);
      reviewItems.push({label: `DESCRIPTION (${format.label.toUpperCase()})`, value: descriptionText});
    }
    const eligibilityText = String(wizard.data.eligibility || '').trim();
    if (eligibilityText) reviewItems.push({label: 'ELIGIBILITY CRITERIA', value: eligibilityText});
    const requiredDocumentsText = String(wizard.data.requiredDocuments || '').trim();
    if (requiredDocumentsText) reviewItems.push({label: 'REQUIRED DOCUMENTS', value: requiredDocumentsText});
    const termsLines = retailWizardTermsLines(wizard);
    if (termsLines.length) reviewItems.push({label: 'TERMS & CONDITIONS', value: termsLines.join(' ')});
    return `<p class="eyebrow">STEP ${total} OF ${total}</p><h1>Review &amp; publish</h1><p class="muted">Review your input and publish the ${esc(wizard.kind)} product.</p><div class="retail-wizard-review">${reviewItems.map(item => `<div class="retail-wizard-review-card"><span class="retail-wizard-review-label">${esc(item.label)}</span><div class="retail-wizard-review-value">${esc(item.value)}</div></div>`).join('')}</div><div class="retail-wizard-ready"><span class="retail-wizard-ready-check">✓</span><div><strong>Ready to publish</strong><small>${esc(wizard.title)} will be published to the knowledge base, with everything above saved under its Overview section.</small></div></div>`;
  }
  return `<p class="eyebrow">STEP ${wizard.step + 1} OF ${total}</p><h1>${esc(step.title)}</h1><p class="retail-wizard-placeholder">This step's design is coming next — tell me what it should collect.</p>`;
}

function wireRetailWizardStepInputs(wizard) {
  if (wizard.step === 0) {
    document.querySelectorAll('.retail-wizard-tag').forEach(button => {
      button.onclick = () => { wizard.data.tag = button.dataset.tag; renderRetailCreateWizardStep(); };
    });
    const input = $('#retailWizardTagInput');
    if (input) input.oninput = () => {
      wizard.data.tag = input.value;
      const next = $('#retailWizardNext'); if (next) next.disabled = !retailWizardStepValid(wizard);
      document.querySelectorAll('.retail-wizard-tag').forEach(button => button.classList.toggle('active', button.dataset.tag === input.value));
    };
    return;
  }
  if (wizard.step === 1) {
    document.querySelectorAll('.retail-wizard-format-pill').forEach(button => {
      button.onclick = () => { wizard.data.description.format = button.dataset.format; renderRetailCreateWizardStep(); };
    });
    if (retailDescriptionIsList(wizard.data.description.format)) {
      document.querySelectorAll('.retail-wizard-desc-item-input').forEach(input => {
        input.oninput = () => {
          const index = Number(input.dataset.index);
          wizard.data.description.items[index] = input.value;
          const next = $('#retailWizardNext'); if (next) next.disabled = !retailWizardStepValid(wizard);
        };
      });
      const addItem = $('#retailWizardAddDescItem');
      if (addItem) addItem.onclick = () => { wizard.data.description.items.push(''); renderRetailCreateWizardStep(); };
    } else {
      const textarea = $('#retailWizardDescriptionInput');
      if (textarea) textarea.oninput = () => {
        wizard.data.description.text = textarea.value;
        const next = $('#retailWizardNext'); if (next) next.disabled = !retailWizardStepValid(wizard);
      };
    }
    return;
  }
  if (wizard.step === 2) {
    const textarea = $('#retailWizardEligibilityInput');
    if (textarea) textarea.oninput = () => {
      wizard.data.eligibility = textarea.value;
      const next = $('#retailWizardNext'); if (next) next.disabled = !retailWizardStepValid(wizard);
    };
    return;
  }
  if (wizard.step === 3) {
    const textarea = $('#retailWizardDocumentsInput');
    if (textarea) textarea.oninput = () => {
      wizard.data.requiredDocuments = textarea.value;
      const next = $('#retailWizardNext'); if (next) next.disabled = !retailWizardStepValid(wizard);
    };
    return;
  }
  if (wizard.step === 4) {
    document.querySelectorAll('.retail-wizard-condition-input').forEach(input => {
      input.oninput = () => {
        const index = Number(input.dataset.index);
        wizard.data.terms[index].text = input.value;
        const next = $('#retailWizardNext'); if (next) next.disabled = !retailWizardStepValid(wizard);
      };
    });
    document.querySelectorAll('.retail-wizard-join-toggle').forEach(button => {
      button.onclick = () => {
        const index = Number(button.dataset.index);
        wizard.data.termJoins[index] = wizard.data.termJoins[index] === 'OR' ? 'AND' : 'OR';
        renderRetailCreateWizardStep();
      };
    });
    const addCondition = $('#retailWizardAddCondition');
    if (addCondition) addCondition.onclick = () => {
      wizard.data.terms.push({text: ''});
      wizard.data.termJoins.push('AND');
      renderRetailCreateWizardStep();
    };
  }
}

function renderRetailCreateWizardStep() {
  const wizard = state.retailCreateWizard; if (!wizard) return;
  setRetailJourneyFocus(true);
  state.selected = null; forgetSelection('retail'); $('#editor').hidden = true; $('#empty').hidden = false;
  $('#empty').className = 'empty retail-wizard-page';
  const total = RETAIL_WIZARD_STEPS.length;
  const icon = retailTitleIconSvg(wizard.title);
  const stepsHtml = RETAIL_WIZARD_STEPS.map((step, index) => {
    const cls = index === wizard.step ? 'current' : index < wizard.step ? 'done' : '';
    const marker = index < wizard.step ? '✓' : index + 1;
    return `<div class="step ${cls}"><b>${marker}</b><span>${esc(step.title)}</span></div>${index < total - 1 ? '<i></i>' : ''}`;
  }).join('');
  const canGoNext = retailWizardStepValid(wizard);
  $('#empty').innerHTML = `<div class="retail-wizard"><div class="retail-wizard-topbar"><div class="retail-wizard-topbar-row"><button class="retail-wizard-back" id="retailWizardBack">‹ Back</button><span class="retail-wizard-product"><span class="retail-create-icon">${icon}</span>${esc(wizard.title)}</span></div><div class="retail-wizard-topbar-row retail-wizard-steps-row"><div class="retail-wizard-steps">${stepsHtml}</div><span class="retail-wizard-count">${wizard.step + 1}/${total}</span></div></div><div class="retail-wizard-progress"><span></span></div><div class="retail-wizard-body">${retailWizardStepBody(wizard)}</div><div class="retail-wizard-footer"><button class="retail-wizard-prev" id="retailWizardPrev" ${wizard.step === 0 ? 'disabled' : ''}>‹ Back</button><button class="retail-wizard-next ${wizard.step === total - 1 ? 'retail-wizard-publish-btn' : ''}" id="retailWizardNext" ${canGoNext ? '' : 'disabled'}>${wizard.step === total - 1 ? `Publish ${esc(wizard.kind)} ✓` : 'Continue ›'}</button></div></div>`;
  $('#retailWizardBack').onclick = () => { state.retailCreateWizard = null; renderRetailConfigure(wizard.path, wizard.parent, wizard.title, wizard.existingRole); };
  $('#retailWizardPrev').onclick = () => { if (wizard.step > 0) { wizard.step -= 1; renderRetailCreateWizardStep(); } };
  $('#retailWizardNext').onclick = () => {
    if (wizard.step < total - 1) { wizard.step += 1; renderRetailCreateWizardStep(); }
    else { publishRetailWizard(wizard); }
  };
  wireRetailWizardStepInputs(wizard);
  alignRetailWizardProgress();
}

function alignRetailWizardProgress() {
  // The blue progress line should end exactly under the current step's
  // label (not a generic step/total percentage), so it visually tracks
  // the uneven widths of "Choose a tag" vs "Required documents" etc.
  const row = document.querySelector('.retail-wizard-steps-row');
  const current = document.querySelector('.retail-wizard-steps .step.current');
  const fill = document.querySelector('.retail-wizard-progress>span');
  if (!row || !current || !fill) return;
  const rowRect = row.getBoundingClientRect();
  const curRect = current.getBoundingClientRect();
  const widthPx = Math.max(0, Math.round(curRect.right - rowRect.left));
  fill.style.width = `${widthPx}px`;
}

function publishRetailWizard(wizard) {
  const button = $('#retailWizardNext'); const back = $('#retailWizardPrev');
  if (button) { button.disabled = true; button.textContent = 'Publishing…'; }
  if (back) back.disabled = true;
  (async () => {
    try {
      const result = await api('/api/data/retail/product', {method:'POST', body:JSON.stringify({title: wizard.title, parent: wizard.parent, existing_role: wizard.existingRole || ''})});
      // Each field becomes its own section (its own box under Overview) rather
      // than one big block of joined text, so Tag / Eligibility criteria /
      // Required documents / Terms & conditions each show up on their own.
      const retailWizardSection = (title, blocks) => ({topic: wizard.title, section: title, variant: retailNorm(title) === 'description' ? 'description' : 'content', section_title: title, path: [title], display_title: title, language: 'en', images: [], content_blocks: blocks});
      const tag = String(wizard.data.tag || '').trim();
      const desc = wizard.data.description || {};
      const eligibilityText = String(wizard.data.eligibility || '').trim();
      const requiredDocumentsText = String(wizard.data.requiredDocuments || '').trim();
      const termTexts = (wizard.data.terms || []).map(term => String(term.text || '').trim()).filter(Boolean);
      const sections = [];
      if (tag) sections.push(retailWizardSection('Tag', [{type: 'paragraph', text: tag}]));
      if (retailDescriptionIsList(desc.format)) {
        const items = (desc.items || []).map(item => String(item || '').trim()).filter(Boolean);
        if (items.length) sections.push(retailWizardSection('Description', [{type: desc.format === 'numbered' ? 'numbered_list' : 'bullet_list', items}]));
      } else {
        const text = String(desc.text || '').trim();
        if (text) sections.push(retailWizardSection('Description', [{type: 'paragraph', text}]));
      }
      if (eligibilityText) sections.push(retailWizardSection('Eligibility criteria', [{type: 'paragraph', text: eligibilityText}]));
      if (requiredDocumentsText) sections.push(retailWizardSection('Required documents', [{type: 'paragraph', text: requiredDocumentsText}]));
      if (termTexts.length) {
        const items = termTexts.map((text, index) => index < termTexts.length - 1 ? `${text} (${wizard.data.termJoins?.[index] || 'AND'})` : text);
        sections.push(retailWizardSection('Terms & conditions', [{type: 'bullet_list', items}]));
      }
      if (sections.length) {
        await api(`/api/data/retail/extracted?path=${encodeURIComponent(result.path)}`, {method:'PUT', body: JSON.stringify({data:{title: wizard.title, sections}})});
      }
      state.retailCreateWizard = null;
      await selectDomain('retail');
      state.retailCollection = wizard.parent; renderFiles();
      await openFile(result.path);
      toast(result.message || `${wizard.title} created`);
    } catch (error) {
      toast(error.message);
      if (button) { button.disabled = false; button.textContent = 'Publish ›'; }
      if (back) back.disabled = false;
    }
  })();
}

function openRetailProductCreate(parent = '', kind = 'product') {
  setRetailJourneyFocus(Boolean(parent));
  state.selected = null; forgetSelection('retail'); $('#editor').hidden = true; $('#empty').hidden = false;
  $('#empty').className = 'empty retail-create-empty';
  $('#empty').innerHTML = `<form class="retail-create-panel retail-create-panel-simple" id="retailProductCreateForm"><div class="retail-create-intro"><span class="retail-create-icon">P</span><h2>Name this ${esc(kind)}</h2></div><label class="retail-product-name">${esc(kind[0].toUpperCase() + kind.slice(1))} name<input id="newRetailProductName" placeholder="For example: ${kind === 'loan' ? 'Education Loan' : 'Youth Savings Account'}" autocomplete="off" required autofocus><span>Use the customer-facing name that should appear in Retail products.</span></label><div class="retail-create-actions"><button type="button" id="cancelRetailProduct" class="secondary">Cancel</button><button type="submit">${parent ? 'Continue' : `Create ${esc(kind)}`} →</button></div></form>`;
  $('#newRetailProductName').focus();
  $('#cancelRetailProduct').onclick = () => { if (parent) return renderRetailCollection(parent); setRetailJourneyFocus(false); $('#empty').innerHTML = '<div class="empty-icon">⌁</div><h2>Select a product to get started</h2><p>Choose an existing product or create a new one.</p>'; };
  $('#retailProductCreateForm').onsubmit = async event => {
    event.preventDefault(); const title = String($('#newRetailProductName').value || '').trim(); if (!title) return;
    try {
      // Only name the product here — it is not created on the backend until
      // the configuration wizard below is completed and published, so
      // cancelling out of naming or the wizard never leaves a stray record.
      // This applies whether the product belongs to a collection (parent set)
      // or is a standalone top-level product (parent empty) — both go through
      // the same configure → wizard → publish journey.
      let existingRole = '';
      if (parent) {
        existingRole = state.retailCollectionRoles[parent] || '';
        const hasStandalone = state.files.some(file => !file.path.includes('/') && retailNorm(file.title || file.name.replace(/\.[^.]+$/, '')) === retailNorm(parent));
        const hasRelatedProducts = state.files.some(file => file.path.startsWith(`${parent}/`));
        if (hasStandalone && !hasRelatedProducts) { existingRole = await chooseRetailExistingRole(); if (!existingRole) return; }
        state.retailCollection = parent; renderFiles();
      }
      renderRetailConfigure(null, parent, title, existingRole);
    } catch (error) { toast(error.message); }
  };
}
$('#addRetailPdf').onclick = () => openRetailProductCreate();
init();
