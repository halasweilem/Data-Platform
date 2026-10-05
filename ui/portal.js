const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const state = { me: null, allDomains: {}, domain: null, files: [], groupPath: [], search: '' };

async function api(url) {
  const response = await fetch(url, {headers: {'Content-Type': 'application/json'}});
  const data = await response.json().catch(() => ({error: 'Invalid server response'}));
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); setTimeout(() => $('#toast').classList.remove('show'), 2600); }

async function init() {
  try {
    const meta = await api('/api/auth/me');
    state.me = meta.user; state.allDomains = meta.all_domains || {};
    $('#user').innerHTML = `${esc(state.me.username)} <b>&middot; ${state.me.roles.length ? 'Data steward' : 'Employee'}</b>`;
    if (state.me.roles.length) { $('#editorLink').hidden = false; $('#editorLink').onclick = () => location = '/index.html'; }
  } catch { location = '/login.html'; return; }

  $('#logout').onclick = async () => { await fetch('/api/auth/logout', {method: 'POST'}); location = '/login.html'; };
  document.querySelectorAll('#portalTabs [data-tab]').forEach(button => button.onclick = () => selectTab(button.dataset.tab));

  renderCategoryCards();
}

function selectTab(tab) {
  document.querySelectorAll('#portalTabs [data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === tab));
  $('#exploreView').hidden = tab !== 'explore';
  $('#assistantView').hidden = tab !== 'assistant';
}

async function renderCategoryCards() {
  const domains = Object.entries(state.allDomains);
  $('#categoryCards').innerHTML = domains.map(([key, value]) => `
    <button class="category-card" data-open="${key}">
      <span class="icon">${esc(value.label[0])}</span>
      <strong>${esc(value.label)}</strong>
      <span id="count-${key}">Loading&hellip;</span>
    </button>`).join('');
  document.querySelectorAll('[data-open]').forEach(button => button.onclick = () => openDomain(button.dataset.open));
  for (const [key] of domains) {
    try { const result = await api(`/api/portal/${key}/files`); $(`#count-${key}`).textContent = `${result.files.length} approved item${result.files.length === 1 ? '' : 's'}`; }
    catch { $(`#count-${key}`).textContent = 'Unavailable'; }
  }
}

async function openDomain(domain) {
  state.domain = domain; state.groupPath = []; state.search = '';
  $('#browsePanel').hidden = false;
  document.querySelectorAll('.category-card').forEach(card => card.classList.toggle('active', card.dataset.open === domain));
  try {
    const result = await api(`/api/portal/${domain}/files`);
    state.files = result.files;
    renderBrowseList();
  } catch { toast('Could not load that area right now.'); }
  $('#browsePanel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

// Build a tree from flat "path/segments/like/this" file paths, honoring how far into the tree state.groupPath has drilled.
function currentLevelEntries() {
  const prefix = state.groupPath.join('/');
  const depth = state.groupPath.length;
  const seenGroups = new Map();
  const leaves = [];
  for (const file of state.files) {
    const parts = file.path.split('/');
    if (prefix && !file.path.startsWith(prefix + '/')) continue;
    if (parts.length - depth === 1) { leaves.push(file); continue; }
    if (parts.length - depth > 1) {
      const key = parts[depth];
      if (!seenGroups.has(key)) seenGroups.set(key, 0);
      seenGroups.set(key, seenGroups.get(key) + 1);
    }
  }
  return {groups: [...seenGroups.entries()], leaves};
}

function renderBrowseList() {
  const {groups, leaves} = currentLevelEntries();
  const query = state.search.trim().toLowerCase();
  const filteredLeaves = query ? leaves.filter(f => f.title.toLowerCase().includes(query)) : leaves;
  const filteredGroups = query ? groups.filter(([name]) => name.toLowerCase().includes(query)) : groups;
  const crumb = state.groupPath.length ? `<div class="browse-crumb"><button id="crumbRoot">${esc(state.allDomains[state.domain]?.label || state.domain)}</button> ${state.groupPath.map(p => `&rsaquo; ${esc(p)}`).join(' ')}</div>` : `<div class="browse-crumb">${esc(state.allDomains[state.domain]?.label || state.domain)}</div>`;
  const rows = [
    ...filteredGroups.map(([name, count]) => `<button class="browse-row" data-group="${esc(name)}"><span>${esc(name)}<small>${count} item${count === 1 ? '' : 's'}</small></span><span class="count">&rsaquo;</span></button>`),
    ...filteredLeaves.map(f => `<button class="browse-row" data-leaf="${esc(f.path)}"><span>${esc(f.title)}</span></button>`),
  ];
  $('#browseList').innerHTML = `
    ${crumb}
    <div class="browse-search"><span>&#8981;</span><input id="browseSearch" placeholder="Search this list" value="${esc(state.search)}"></div>
    ${rows.length ? rows.join('') : '<div class="no-results">Nothing matches here.</div>'}`;
  $('#browseSearch').oninput = event => { state.search = event.target.value; renderBrowseList(); };
  $('#browseSearch').focus(); $('#browseSearch').selectionStart = $('#browseSearch').selectionEnd = state.search.length;
  if ($('#crumbRoot')) $('#crumbRoot').onclick = () => { state.groupPath = []; state.search = ''; renderBrowseList(); };
  document.querySelectorAll('[data-group]').forEach(button => button.onclick = () => { state.groupPath = [...state.groupPath, button.dataset.group]; state.search = ''; renderBrowseList(); });
  document.querySelectorAll('[data-leaf]').forEach(button => button.onclick = () => openLeaf(button.dataset.leaf));
}

async function openLeaf(path) {
  document.querySelectorAll('[data-leaf]').forEach(button => button.classList.toggle('active', button.dataset.leaf === path));
  $('#browseDetail').innerHTML = '<div class="placeholder"><p>Loading&hellip;</p></div>';
  try {
    if (state.domain === 'retail') await renderRetailDetail(path);
    else await renderStepsDetail(path);
  } catch { $('#browseDetail').innerHTML = '<div class="placeholder"><p>Could not load this item.</p></div>'; }
}

// ---- Retail: flat sections[] with "A > B > C" section_title paths -> render as a nested read-only outline ----
async function renderRetailDetail(path) {
  const {data} = await api(`/api/portal/retail/extracted?path=${encodeURIComponent(path)}`);
  const root = {children: new Map()};
  for (const section of data.sections || []) {
    const parts = (section.section_title || section.title || 'Untitled').split('>').map(part => part.trim()).filter(Boolean);
    let node = root;
    parts.forEach((part, index) => {
      if (!node.children.has(part)) node.children.set(part, {children: new Map(), content: null});
      node = node.children.get(part);
      if (index === parts.length - 1) node.content = section.content || '';
    });
  }
  const renderNode = (name, node, depth) => {
    const hasBody = node.content || node.children.size;
    const children = [...node.children.entries()].map(([childName, child]) => renderNode(childName, child, depth + 1)).join('');
    return `<div class="outline-node depth-${depth} ${depth === 0 ? 'open' : ''}">
      <div class="outline-head" onclick="this.parentElement.classList.toggle('open')"><span>${esc(name)}</span>${hasBody ? '<span class="chev">&rsaquo;</span>' : ''}</div>
      <div class="outline-body">${node.content ? `<p class="outline-text">${esc(node.content)}</p>` : ''}${children}</div>
    </div>`;
  };
  const body = [...root.children.entries()].map(([name, node]) => renderNode(name, node, 0)).join('') || '<p class="muted">No published content in this document yet.</p>';
  $('#browseDetail').innerHTML = `
    <p class="detail-eyebrow">RETAIL PRODUCT</p>
    <h1 class="detail-title">${esc(data.title || path)}</h1>
    <p class="detail-meta">${(data.sections || []).length} published section${(data.sections || []).length === 1 ? '' : 's'}</p>
    ${body}
    <div class="source-note">Source: <b>${esc(path)}</b> &middot; retail products</div>`;
}

// ---- Steps: workflow / faq / tips shapes -> render read-only ----
async function renderStepsDetail(path) {
  const {data} = await api(`/api/portal/steps/file?path=${encodeURIComponent(path)}`);
  const directory = path.split('/').slice(0, -1).join('/');
  const imageUrl = name => `/api/portal/steps/asset?path=${encodeURIComponent(directory + '/' + name)}`;
  let body = '';
  if (Array.isArray(data)) {
    body = data.map(item => `<div class="faq-item"><strong>${esc(item.question || '')}</strong><p class="outline-text">${esc(item.answer || '')}</p></div>`).join('') || '<p class="muted">No published entries yet.</p>';
  } else if (Array.isArray(data.steps)) {
    body = data.steps.map((step, index) => `<div class="step-row"><span class="step-num">${esc(step.label ?? step.step ?? index + 1)}</span><div><p class="outline-text">${esc(step.text || '')}</p>${step.image ? `<img src="${imageUrl(step.image)}" loading="lazy" onerror="this.remove()">` : ''}</div></div>`).join('') || '<p class="muted">No published steps yet.</p>';
  } else if (Array.isArray(data.tips)) {
    body = data.tips.map(tip => `<div class="faq-item"><p class="outline-text">${esc(tip.text || tip)}</p>${Array.isArray(tip.substeps) ? tip.substeps.map(sub => `<p class="outline-text" style="padding-left:16px">${esc(sub)}</p>`).join('') : ''}</div>`).join('') || '<p class="muted">No published tips yet.</p>';
  } else {
    body = `<pre class="outline-text">${esc(JSON.stringify(data, null, 2))}</pre>`;
  }
  $('#browseDetail').innerHTML = `
    <p class="detail-eyebrow">STEPS &amp; WORKFLOWS</p>
    <h1 class="detail-title">${esc(data.title || path.split('/').pop())}</h1>
    <p class="detail-meta">Published content</p>
    ${body}
    <div class="source-note">Source: <b>${esc(path)}</b> &middot; steps &amp; workflows</div>`;
}

init();
