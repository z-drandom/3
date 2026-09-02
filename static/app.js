/* ================= EHS 知识库 前端（原生 JS，无外部依赖） =================
   只读视图：目录树 / 正文 / 全文搜索 / 标签过滤 / 到期提醒
   管理视图：口令进入后可 新建 / 编辑 frontmatter / 上传 / 删除 / 看日志与回收站
========================================================================= */
const App = (() => {
  const S = {                       // 全局状态
    tree: [], tags: [], stats: {},
    curPath: null, curTag: '', curStatus: '',
    token: sessionStorage.getItem('ehs_token') || '',
    operator: sessionStorage.getItem('ehs_operator') || '',
    version: -1, openDirs: new Set(JSON.parse(sessionStorage.getItem('ehs_dirs') || '[]')),
  };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  /* ---------- 网络请求：管理接口自动带上口令与操作人 ---------- */
  async function api(url, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (S.token) {
      headers['X-Admin-Token'] = S.token;
      // 中文姓名不能直接放进 HTTP 头（头仅支持 latin-1），先做百分号编码
      headers['X-Admin-User'] = encodeURIComponent(S.operator || 'admin');
    }
    if (opts.json) { headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(opts.json); opts.method = opts.method || 'POST'; }
    const res = await fetch(url, Object.assign({}, opts, { headers }));
    const text = await res.text();
    let data; try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
    if (!res.ok) throw new Error(data.detail || ('请求失败 ' + res.status));
    return data;
  }
  function toast(msg, ms = 2200) {
    const t = $('toast'); t.textContent = msg; t.classList.remove('hidden');
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('hidden'), ms);
  }
  const isAdmin = () => !!S.token;

  /* ---------- 目录树 ---------- */
  function renderTree() {
    const html = S.tree.map(n => nodeHTML(n)).join('') || '<div class="placeholder">知识库为空，请在 docs 目录下放入 .md 文件</div>';
    $('paneTree').innerHTML = html;
  }
  function nodeHTML(node) {
    const open = S.openDirs.has(node.path);
    const kids = (node.children || []).map(nodeHTML).join('');
    const docs = (node.docs || []).map(docHTML).join('');
    return `<div class="node">
      <div class="node-dir" onclick="App.toggleDir('${esc(node.path)}',this)">
        <span class="caret ${open ? 'open' : ''}">▶</span>
        <span>${esc(node.name)}</span><span class="cnt">(${node.count})</span>
      </div>
      <div class="node-children ${open ? '' : 'hidden'}">${kids}${docs}</div>
    </div>`;
  }
  function docHTML(d) {
    const cls = 'doc-item' + (d.status === '废止' ? ' void' : '') + (d.path === S.curPath ? ' active' : '');
    const dot = d.review_days !== null && d.review_days < 30 ? (d.review_days < 0 ? ' 🔴' : ' 🟠') : '';
    return `<a class="${cls}" title="${esc(d.path)}" onclick="App.openDoc('${esc(d.path)}')">${esc(d.title)}${dot}</a>`;
  }
  function toggleDir(path, el) {
    if (S.openDirs.has(path)) S.openDirs.delete(path); else S.openDirs.add(path);
    sessionStorage.setItem('ehs_dirs', JSON.stringify([...S.openDirs]));
    el.querySelector('.caret').classList.toggle('open');
    el.nextElementSibling.classList.toggle('hidden');
  }

  /* ---------- 标签 ---------- */
  function renderTags() {
    $('paneTags').innerHTML = '<div class="tagcloud">' + S.tags.map(t =>
      `<span class="tagchip ${t.tag === S.curTag ? 'active' : ''}" onclick="App.filterTag('${esc(t.tag)}')">${esc(t.tag)} ${t.count}</span>`
    ).join('') + '</div>';
  }
  async function filterTag(tag) {
    S.curTag = (S.curTag === tag) ? '' : tag;
    renderTags();
    if (!S.curTag) return goHome();
    const r = await api(`/api/docs?tag=${encodeURIComponent(S.curTag)}&status=${encodeURIComponent(S.curStatus)}`);
    renderResults(r.results, `标签「${S.curTag}」共 ${r.results.length} 篇`);
  }

  /* ---------- 搜索 ---------- */
  async function doSearch() {
    const q = $('q').value.trim();
    if (!q) return goHome();
    const url = `/api/search?q=${encodeURIComponent(q)}&tag=${encodeURIComponent(S.curTag)}&status=${encodeURIComponent(S.curStatus)}&limit=60`;
    const r = await api(url);
    renderResults(r.results, `“${esc(q)}” 命中 ${r.count} 篇`, true);
  }
  function renderResults(list, title, showSnip) {
    if (!list.length) { $('content').innerHTML = `<div class="placeholder">${title}<br>没有匹配的文档</div>`; return; }
    $('content').innerHTML = `<h2 style="margin:0 0 14px;font-size:16px;color:#6b7280">${title}</h2>` +
      list.map(d => `<div class="result" onclick="App.openDoc('${esc(d.path)}')">
        <h3>${d.title_html || esc(d.title)} ${statusPill(d)}</h3>
        <div class="path">${esc(d.path)} · 责任人 ${esc(d.owner || '—')} · 生效 ${esc(d.effective_date || '—')} · 复审 ${esc(d.review_date || '—')} ${duePill(d)}</div>
        ${showSnip ? (d.snippets || []).map(s => `<div class="snippet">${s}</div>`).join('') : ''}
        <div class="path">${(d.tags || []).map(t => '#' + esc(t)).join(' ')}</div>
      </div>`).join('');
  }
  const statusPill = (d) => d.status === '废止' ? '<span class="pill void">废止</span>' : '<span class="pill ok">生效</span>';
  function duePill(d) {
    if (d.review_days === null || d.review_days === undefined) return '';
    if (d.review_days < 0) return `<span class="pill overdue">已逾期 ${-d.review_days} 天</span>`;
    if (d.review_days <= 7) return `<span class="pill urgent">${d.review_days} 天后复审</span>`;
    if (d.review_days < 30) return `<span class="pill warn">${d.review_days} 天后复审</span>`;
    return '';
  }

  /* ---------- 正文 ---------- */
  async function openDoc(path) {
    try {
      const d = await api('/api/doc?path=' + encodeURIComponent(path));
      S.curPath = path; renderTree();
      const attach = d.attachments.length ? `<div class="attach"><h4>附件（${d.attachments.length}）</h4>` +
        d.attachments.map(a => `<a href="/api/asset/${encodeURI(a.path)}?download=1" target="_blank">📎 ${esc(a.name)} (${(a.size / 1024).toFixed(0)} KB)</a>`).join('') + '</div>' : '';
      $('content').innerHTML = adminBar(path) + `
        <div class="doc-head">
          <h1 class="doc-title">${esc(d.title)}</h1>
          <div class="meta-row">
            ${statusPill(d)} ${duePill(d)}
            <span>分类：${esc(d.category)}</span>
            <span>责任人：${esc(d.owner || '—')}</span>
            <span>生效日期：${esc(d.effective_date || '—')}</span>
            <span>复审日期：${esc(d.review_date || '—')}</span>
            <span>标签：${(d.tags || []).map(t => '#' + esc(t)).join(' ') || '—'}</span>
            <span>文件：${esc(d.path)}</span>
          </div>
        </div>
        <article class="markdown">${d.html}</article>${attach}`;
      $('content').scrollTop = 0;
      window.__doc = d;
    } catch (e) { toast(e.message); refresh(); }
  }
  function adminBar(path) {
    if (!isAdmin()) return '';
    return `<div class="admin-bar">
      <span class="who">管理视图（操作人：${esc(S.operator || 'admin')}）</span>
      <button class="btn tiny" onclick="App.editDoc('${esc(path)}')">编辑</button>
      <button class="btn tiny" onclick="App.uploadTo('${esc(path.replace(/\.[^.]+$/, ''))}')">上传附件</button>
      <button class="btn tiny danger" onclick="App.delDoc('${esc(path)}')">删除</button>
    </div>`;
  }
  function goHome() {
    S.curPath = null; $('q').value = ''; renderTree();
    api(`/api/docs?status=${encodeURIComponent(S.curStatus)}&limit=50`).then(r =>
      renderResults(r.results, `最近更新的 ${r.results.length} 篇（共 ${S.stats.total || 0} 篇）`));
  }

  /* ---------- 到期提醒抽屉 ---------- */
  async function showReviews() {
    const r = await api('/api/reviews');
    $('drawerTitle').textContent = `到期提醒（${r.days} 天内 / 已逾期）`;
    $('drawerBody').innerHTML = r.results.length ? r.results.map(d => `
      <div class="due-item ${d.level}" onclick="App.openDoc('${esc(d.path)}')">
        <div class="t">${esc(d.title)}</div>
        <div class="s">复审日 ${esc(d.review_date)} ·
          ${d.review_days < 0 ? '已逾期 ' + (-d.review_days) : '剩余 ' + d.review_days} 天 ·
          责任人 ${esc(d.owner || '—')}</div>
      </div>`).join('') : '<div class="placeholder">30 天内没有需要复审的文档 ✅</div>';
    $('drawer').classList.remove('hidden');
  }
  const closeDrawer = () => $('drawer').classList.add('hidden');

  /* ---------- 管理视图 ---------- */
  function openModal(html) { $('modal').innerHTML = html; $('modalMask').classList.remove('hidden'); }
  function closeModal(ev) { if (!ev || ev.target === $('modalMask')) $('modalMask').classList.add('hidden'); }
  const hideModal = () => $('modalMask').classList.add('hidden');

  function adminEntry() {
    if (isAdmin()) return showAdminPanel();
    openModal(`<h3>进入管理视图</h3>
      <div class="tip">口令由服务器环境变量 EHS_ADMIN_TOKEN 设置。同事只读浏览无需登录。</div>
      <div class="form-row"><label>口令</label><input id="mPwd" type="password" autofocus></div>
      <div class="form-row"><label>操作人</label><input id="mUser" placeholder="记入 operation.log，如 张三" value="${esc(S.operator)}"></div>
      <div class="modal-foot"><button class="btn" onclick="App.closeModal()">取消</button>
      <button class="btn primary" onclick="App.login()">进入</button></div>`);
    $('mPwd').addEventListener('keydown', e => { if (e.key === 'Enter') login(); });
  }
  async function login() {
    const pwd = $('mPwd').value;
    try {
      await api('/api/admin/login', { json: { password: pwd } });
      S.token = pwd; S.operator = $('mUser').value.trim() || 'admin';
      sessionStorage.setItem('ehs_token', S.token); sessionStorage.setItem('ehs_operator', S.operator);
      hideModal(); toast('已进入管理视图'); $('btnAdmin').textContent = '管理面板';
      if (S.curPath) openDoc(S.curPath); else goHome();
    } catch (e) { toast(e.message); }
  }
  function logout() {
    S.token = ''; sessionStorage.removeItem('ehs_token');
    $('btnAdmin').textContent = '管理视图'; closeDrawer(); toast('已退出管理视图');
    if (S.curPath) openDoc(S.curPath); else goHome();
  }
  async function showAdminPanel() {
    $('drawerTitle').textContent = '管理面板';
    $('drawer').classList.remove('hidden');
    $('drawerBody').innerHTML = `
      <div class="form-row" style="flex-wrap:wrap;gap:6px">
        <button class="btn tiny primary" onclick="App.newDoc()">＋ 新建文档</button>
        <button class="btn tiny" onclick="App.uploadTo('')">⬆ 上传文件</button>
        <button class="btn tiny" onclick="App.newDir()">📁 新建分类</button>
        <button class="btn tiny" onclick="App.reindex()">↻ 重建索引</button>
        <button class="btn tiny" onclick="App.logout()">退出</button>
      </div>
      <div id="adminTabs" class="side-tabs" style="margin:8px 0">
        <button class="tab active" onclick="App.adminTab('log',this)">操作日志</button>
        <button class="tab" onclick="App.adminTab('trash',this)">回收站</button>
        <button class="tab" onclick="App.adminTab('err',this)">解析异常</button>
      </div><div id="adminPane"></div>`;
    adminTab('log');
  }
  async function adminTab(name, el) {
    if (el) { [...el.parentNode.children].forEach(b => b.classList.remove('active')); el.classList.add('active'); }
    const pane = $('adminPane');
    try {
      if (name === 'log') {
        const r = await api('/api/admin/log?limit=200');
        pane.innerHTML = '<table class="logtable"><tr><th>时间</th><th>操作</th><th>目标</th><th>人</th></tr>' +
          r.rows.map(x => `<tr><td>${esc(x.time)}</td><td>${esc(x.action)}</td><td>${esc(x.target)}</td><td>${esc(x.operator)}</td></tr>`).join('') + '</table>';
      } else if (name === 'trash') {
        const r = await api('/api/admin/trash');
        pane.innerHTML = `<div class="tip">删除的文件先移入 .trash，保留 ${r.retain_days} 天后自动清理；期间可随时还原。</div>` +
          (r.rows.length ? r.rows.map(x => `<div class="due-item"><div class="t">${esc(x.original)}</div>
            <div class="s">删除于 ${esc(x.deleted_at)} · ${(x.size / 1024).toFixed(0)} KB
            <button class="btn tiny" style="float:right" onclick="App.restore('${esc(x.entry)}')">还原</button></div></div>`).join('')
            : '<div class="placeholder">回收站为空</div>');
      } else {
        const r = await api('/api/admin/errors');
        pane.innerHTML = r.errors.length ? r.errors.map(e => `<div class="due-item overdue"><div class="t">${esc(e.path)}</div><div class="s">${esc(e.error)}</div></div>`).join('')
          : '<div class="placeholder">全部 Markdown 解析正常 ✅</div>';
      }
    } catch (e) { toast(e.message); }
  }

  /* ---------- 新建 / 编辑 / 删除 / 上传 ---------- */
  function docForm(d, isNew) {
    return `<h3>${isNew ? '新建文档' : '编辑：' + esc(d.path)}</h3>
      <div class="tip">保存后直接写入磁盘上的 .md 文件；frontmatter 字段与正文都可修改。</div>
      <div class="form-row"><label>文件路径</label><input id="fPath" value="${esc(d.path || '')}" ${isNew ? '' : 'readonly'} placeholder="危化品/示例文档.md"></div>
      <div class="form-row"><label>标题</label><input id="fTitle" value="${esc(d.title || '')}"></div>
      <div class="form-row"><label>分类</label><input id="fCat" value="${esc(d.category || '')}"></div>
      <div class="form-row"><label>标签</label><input id="fTags" value="${esc((d.tags || []).join(', '))}" placeholder="逗号分隔"></div>
      <div class="form-row"><label>责任人</label><input id="fOwner" value="${esc(d.owner || '')}"></div>
      <div class="form-row"><label>生效日期</label><input id="fEff" type="date" value="${esc(d.effective_date || '')}"></div>
      <div class="form-row"><label>复审日期</label><input id="fRev" type="date" value="${esc(d.review_date || '')}"></div>
      <div class="form-row"><label>状态</label><select id="fStatus">
        <option ${d.status !== '废止' ? 'selected' : ''}>生效</option>
        <option ${d.status === '废止' ? 'selected' : ''}>废止</option></select></div>
      <div class="form-row"><label>正文</label><textarea id="fBody">${esc(d.markdown || '')}</textarea></div>
      <div class="modal-foot"><button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn primary" onclick="App.saveDoc(${isNew})">保存到磁盘</button></div>`;
  }
  function newDoc() {
    const today = new Date().toISOString().slice(0, 10);
    const next = new Date(Date.now() + 365 * 864e5).toISOString().slice(0, 10);
    openModal(docForm({ path: '', effective_date: today, review_date: next, status: '生效',
      markdown: '## 适用范围\n\n## 主要风险\n\n## 管控措施\n\n| 项目 | 要求 | 频次 |\n| --- | --- | --- |\n|  |  |  |\n' }, true));
  }
  async function editDoc(path) {
    const d = await api('/api/doc?path=' + encodeURIComponent(path));
    openModal(docForm(d, false));
  }
  async function saveDoc(isNew) {
    const payload = {
      path: $('fPath').value.trim(), title: $('fTitle').value.trim(),
      category: $('fCat').value.trim(),
      tags: $('fTags').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
      owner: $('fOwner').value.trim(), effective_date: $('fEff').value,
      review_date: $('fRev').value, status: $('fStatus').value,
      body: $('fBody').value, create: !!isNew,
    };
    if (!payload.path.endsWith('.md')) return toast('文件路径必须以 .md 结尾');
    try {
      const r = await api('/api/admin/save', { json: payload });
      hideModal(); toast('已保存到磁盘'); await refresh(); openDoc(r.path);
    } catch (e) { toast(e.message); }
  }
  async function delDoc(path) {
    if (!confirm(`确认删除？\n\n${path}\n\n文件将移入 .trash 并记入 operation.log，保留 90 天，可随时还原。`)) return;
    try {
      await api('/api/admin/delete', { json: { path } });
      toast('已移入回收站'); S.curPath = null; await refresh(); goHome();
    } catch (e) { toast(e.message); }
  }
  async function restore(entry) {
    try { await api('/api/admin/restore', { json: { path: entry } }); toast('已还原'); await refresh(); adminTab('trash'); }
    catch (e) { toast(e.message); }
  }
  function uploadTo(dir) {
    openModal(`<h3>上传文件</h3>
      <div class="tip">上传 .md 即新增知识条目；上传 PDF/图片/Excel 到「与文档同名的文件夹」即成为该文档的附件。</div>
      <div class="form-row"><label>目标目录</label><input id="uDir" value="${esc(dir)}" placeholder="如 危化品/硫酸储罐区安全管理规定"></div>
      <div class="form-row"><label>选择文件</label><input id="uFiles" type="file" multiple></div>
      <div class="modal-foot"><button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn primary" onclick="App.doUpload()">上传</button></div>`);
  }
  async function doUpload() {
    const files = $('uFiles').files;
    if (!files.length) return toast('请先选择文件');
    const fd = new FormData();
    fd.append('dir', $('uDir').value.trim());
    for (const f of files) fd.append('files', f);
    try {
      const r = await api('/api/admin/upload', { method: 'POST', body: fd });
      hideModal(); toast(`已上传 ${r.saved.length} 个文件`); refresh();
    } catch (e) { toast(e.message); }
  }
  async function newDir() {
    const name = prompt('新建分类目录（相对 docs 根目录，如：承包商管理）');
    if (!name) return;
    try { await api('/api/admin/mkdir', { json: { path: name } }); toast('已创建'); refresh(); }
    catch (e) { toast(e.message); }
  }
  async function reindex() {
    try { const r = await api('/api/admin/reindex', { json: {} }); toast(`已重建索引：${r.total} 篇`); refresh(); }
    catch (e) { toast(e.message); }
  }

  /* ---------- 数据刷新与轮询 ---------- */
  async function refresh() {
    const [t, g, s, r] = await Promise.all([
      api('/api/tree'), api('/api/tags'), api('/api/stats'), api('/api/reviews')]);
    S.tree = t.tree; S.tags = g.tags; S.stats = s; S.version = s.version;
    renderTree(); renderTags();
    $('dueCount').textContent = r.results.length;
    $('stats').innerHTML = `共 ${s.total} 篇 · 生效 ${s.active} · 废止 ${s.void}<br>
      分类 ${s.categories} · 标签 ${s.tags} · 待复审 ${s.due}<br>
      索引更新：${esc(s.built_at)}`;
  }
  // 每 8 秒对比索引版本号：磁盘上的增删改会在这里自动反映到页面
  async function poll() {
    try {
      const s = await api('/api/stats');
      if (s.version !== S.version) {
        await refresh();
        if (S.curPath) { if (S.stats && !document.querySelector('.doc-title')) return; openDoc(S.curPath).catch(() => goHome()); }
        toast('检测到文件变化，已自动刷新');
      }
    } catch { /* 服务重启中，忽略 */ }
  }

  /* ---------- 初始化 ---------- */
  function init() {
    $('btnSearch').onclick = doSearch;
    $('q').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
    $('btnReview').onclick = showReviews;
    $('btnAdmin').onclick = adminEntry;
    $('statusFilter').onchange = (e) => { S.curStatus = e.target.value; $('q').value ? doSearch() : goHome(); };
    document.querySelectorAll('.side-tabs .tab[data-tab]').forEach(btn => {
      btn.onclick = () => {
        document.querySelectorAll('.side-tabs .tab[data-tab]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        $('paneTree').classList.toggle('hidden', btn.dataset.tab !== 'tree');
        $('paneTags').classList.toggle('hidden', btn.dataset.tab !== 'tags');
      };
    });
    if (isAdmin()) $('btnAdmin').textContent = '管理面板';
    refresh().then(goHome).catch(e => { $('content').innerHTML = `<div class="placeholder">加载失败：${esc(e.message)}</div>`; });
    setInterval(poll, 8000);
  }
  document.addEventListener('DOMContentLoaded', init);

  return { openDoc, toggleDir, filterTag, goHome, showReviews, closeDrawer, closeModal,
           login, logout, adminTab, newDoc, editDoc, saveDoc, delDoc, restore,
           uploadTo, doUpload, newDir, reindex };
})();
