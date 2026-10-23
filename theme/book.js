const drawer = document.querySelector('.chapter-drawer');
if (matchMedia('(max-width: 760px)').matches) drawer.open = false;
const dialog = document.querySelector('.search-dialog');
const input = dialog.querySelector('input');
const status = dialog.querySelector('.search-status');
const results = dialog.querySelector('.search-results');
const root = new URL(document.body.dataset.root, location.href);
let index;
let sequence = 0;
function openSearch() { dialog.showModal(); input.focus(); }
document.querySelector('.search-open').addEventListener('click', openSearch);
document.querySelector('.search-close').addEventListener('click', () => dialog.close());
document.addEventListener('keydown', event => {
  if (event.key === '/' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName) && !document.activeElement.isContentEditable) {
    event.preventDefault(); if (!dialog.open) openSearch();
  }
});
input.addEventListener('input', async () => {
  const current = ++sequence;
  const query = input.value.trim().toLowerCase();
  results.replaceChildren();
  if (!query) { status.textContent = '输入关键词搜索全部教程'; return; }
  status.textContent = '正在搜索…';
  try {
    index ??= fetch(new URL('search/search_index.json', root)).then(r => { if (!r.ok) throw Error(); return r.json(); }).catch(e => { index = undefined; throw e; });
    const data = await index;
    if (current !== sequence) return;
    const words = query.split(/\s+/);
    const matches = data.docs.filter(d => words.every(w => (d.title + ' ' + d.text).toLowerCase().includes(w)))
      .sort((a,b) => Number(b.title.toLowerCase().includes(query)) - Number(a.title.toLowerCase().includes(query)));
    status.textContent = `${matches.length} 条结果${matches.length > 30 ? '，显示前 30 条；可增加关键词缩小范围' : ''}`;
    matches.slice(0,30).forEach(d => {
      const li = document.createElement('li'); const a = document.createElement('a'); const p = document.createElement('p');
      a.href = new URL(d.location, root).href; a.textContent = d.title;
      const start = Math.max(0, d.text.toLowerCase().indexOf(words[0]) - 35);
      p.textContent = (start ? '…' : '') + d.text.slice(start, start + 150) + '…';
      li.append(a,p); results.append(li);
    });
  } catch { if(current === sequence) status.textContent = '搜索索引加载失败，请重新输入或刷新页面。'; }
});
document.querySelectorAll('pre').forEach(pre => {
  const code = pre.querySelector('code'); if (!code) return;
  const button = document.createElement('button'); button.className = 'copy'; button.textContent = '复制'; button.setAttribute('aria-label','复制代码');
  button.addEventListener('click', async () => { try { await navigator.clipboard.writeText(code.textContent); button.textContent = '已复制'; } catch { button.textContent = '请手动复制'; } }); pre.append(button);
});
document.querySelectorAll('article table').forEach(table => { const wrap = document.createElement('div'); wrap.className = 'table-scroll'; table.before(wrap); wrap.append(table); });
