"""HTML template for the browsable curl reference.

Kept apart from the generator so the markup stays readable. The page is a thin
shell: endpoint data is embedded as JSON and rendered client-side, which keeps
the Python side to a single json.dumps and lets the page filter and re-render
commands as the reader edits variables.
"""

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — API reference</title>
<style>
:root {
  --bg: #fbfbfa; --panel: #ffffff; --ink: #1a1a19; --muted: #6b6b68;
  --line: #e4e4e1; --code-bg: #f5f5f3; --accent: #7c5cff;
  --get: #2f7d4f; --post: #1f6feb; --patch: #9a6700; --put: #9a6700; --delete: #b42318;
  --shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.04);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16161a; --panel: #1d1d22; --ink: #ececec; --muted: #9a9aa0;
    --line: #2e2e35; --code-bg: #121216; --accent: #a48bff;
    --get: #5fd08a; --post: #6ea8ff; --patch: #e3b341; --put: #e3b341; --delete: #ff7b72;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
code, pre, .mono { font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace; }
header {
  position: sticky; top: 0; z-index: 20; background: var(--panel);
  border-bottom: 1px solid var(--line); padding: 14px 24px;
}
.title { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.title h1 { margin: 0; font-size: 17px; letter-spacing: -.01em; }
.title .sub { color: var(--muted); font-size: 13px; }
.vars { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
.field { display: flex; flex-direction: column; gap: 3px; }
.field label { font-size: 10px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); }
.field input {
  background: var(--code-bg); color: var(--ink); border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 9px; font-size: 12.5px; min-width: 190px;
  font-family: "SF Mono", ui-monospace, Menlo, monospace;
}
.field input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
#filter { min-width: 260px; }
main { max-width: 1080px; margin: 0 auto; padding: 24px; }
.hint {
  background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--accent);
  border-radius: 8px; padding: 12px 16px; margin-bottom: 22px; font-size: 13.5px; color: var(--muted);
}
.hint b { color: var(--ink); }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted);
     margin: 30px 0 12px; padding-bottom: 7px; border-bottom: 1px solid var(--line); }
.ep { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
      margin-bottom: 12px; overflow: hidden; box-shadow: var(--shadow); }
.ep-head { display: flex; align-items: center; gap: 11px; padding: 12px 15px; cursor: pointer; }
.ep-head:hover { background: var(--code-bg); }
.method { font-size: 10.5px; font-weight: 700; letter-spacing: .06em; padding: 3px 7px;
          border-radius: 4px; border: 1px solid currentColor; min-width: 58px; text-align: center; }
.GET { color: var(--get); } .POST { color: var(--post); }
.PATCH, .PUT { color: var(--patch); } .DELETE { color: var(--delete); }
.path { font-size: 13.5px; font-weight: 500; }
.summary { color: var(--muted); font-size: 12.5px; margin-left: auto; text-align: right; }
.lock { font-size: 11px; color: var(--muted); }
.ep-body { display: none; border-top: 1px solid var(--line); padding: 14px 15px; }
.ep.open .ep-body { display: block; }
.desc { color: var(--muted); font-size: 13px; margin: 0 0 12px; white-space: pre-wrap; }
.cmd { position: relative; }
pre { background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px;
      padding: 13px 15px; margin: 0; overflow-x: auto; font-size: 12.5px; line-height: 1.6; }
.copy { position: absolute; top: 8px; right: 8px; background: var(--panel); color: var(--muted);
        border: 1px solid var(--line); border-radius: 6px; padding: 4px 10px; font-size: 11.5px;
        cursor: pointer; }
.copy:hover { color: var(--ink); border-color: var(--accent); }
.copy.done { color: var(--get); border-color: var(--get); }
.params { margin-top: 11px; font-size: 12.5px; color: var(--muted); }
.params b { color: var(--ink); font-weight: 500; }
.params span { display: inline-block; background: var(--code-bg); border: 1px solid var(--line);
               border-radius: 4px; padding: 1px 6px; margin: 2px 3px 2px 0; font-size: 11.5px; }
footer { color: var(--muted); font-size: 12px; text-align: center; padding: 30px 0 40px; }
.empty { color: var(--muted); padding: 30px; text-align: center; }
</style>
</head>
<body>
<header>
  <div class="title">
    <h1>__TITLE__</h1>
    <span class="sub">__COUNT__ endpoints · generated from the live routes by <span class="mono">make api</span></span>
  </div>
  <div class="vars" id="vars"></div>
</header>

<main>
  <div class="hint">
    <b>Postman:</b> copy any command, then <b>Import → Raw text</b> and paste.
    Set the variables above once — they are substituted into every command and remembered in this browser.
    Get a token with the <span class="mono">POST /api/v1/auth/login</span> command below.
  </div>
  <div id="list"></div>
  <div class="empty" id="empty" hidden>Nothing matches that filter.</div>
</main>

<footer>Auto-generated — do not edit by hand. Regenerate with <span class="mono">make api</span>.</footer>

<script>
const DATA = __DATA__;
const VARS = __VARS__;
const store = {};

function loadVars() {
  VARS.forEach(v => {
    let saved = null;
    try { saved = localStorage.getItem('sl:' + v.name); } catch (e) { saved = null; }
    store[v.name] = saved !== null ? saved : v.value;
  });
}

function renderVars() {
  const box = document.getElementById('vars');
  box.innerHTML = '';
  VARS.forEach(v => {
    const field = document.createElement('div');
    field.className = 'field';
    const label = document.createElement('label');
    label.textContent = v.label;
    const input = document.createElement('input');
    input.value = store[v.name];
    input.placeholder = v.value;
    if (v.name === 'filter') input.id = 'filter';
    input.addEventListener('input', () => {
      store[v.name] = input.value;
      try { localStorage.setItem('sl:' + v.name, input.value); } catch (e) { /* private mode */ }
      renderList();
    });
    field.append(label, input);
    box.append(field);
  });
}

function substitute(text) {
  let out = text;
  VARS.forEach(v => {
    const value = store[v.name] || v.value;
    out = out.split('{{' + v.name + '}}').join(value);
  });
  return out;
}

function renderList() {
  const term = (document.getElementById('search') ? document.getElementById('search').value : '').toLowerCase();
  const list = document.getElementById('list');
  list.innerHTML = '';
  let shown = 0;

  DATA.forEach(group => {
    const matches = group.endpoints.filter(e =>
      !term || (e.method + ' ' + e.path + ' ' + e.summary).toLowerCase().includes(term));
    if (!matches.length) return;
    shown += matches.length;

    const h2 = document.createElement('h2');
    h2.textContent = group.tag + ' · ' + matches.length;
    list.append(h2);

    matches.forEach(e => {
      const ep = document.createElement('div');
      ep.className = 'ep';

      const head = document.createElement('div');
      head.className = 'ep-head';
      head.innerHTML = '<span class="method ' + e.method + '">' + e.method + '</span>' +
                       '<span class="path mono">' + e.path + '</span>' +
                       (e.auth ? '<span class="lock" title="Requires a bearer token">&#128274;</span>' : '') +
                       '<span class="summary">' + e.summary + '</span>';
      head.onclick = () => ep.classList.toggle('open');

      const body = document.createElement('div');
      body.className = 'ep-body';
      if (e.description) {
        const d = document.createElement('p');
        d.className = 'desc';
        d.textContent = e.description;
        body.append(d);
      }

      const cmd = document.createElement('div');
      cmd.className = 'cmd';
      const pre = document.createElement('pre');
      const rendered = substitute(e.curl);
      pre.textContent = rendered;
      const copy = document.createElement('button');
      copy.className = 'copy';
      copy.textContent = 'Copy';
      copy.onclick = ev => {
        ev.stopPropagation();
        navigator.clipboard.writeText(rendered).then(() => {
          copy.textContent = 'Copied';
          copy.classList.add('done');
          setTimeout(() => { copy.textContent = 'Copy'; copy.classList.remove('done'); }, 1400);
        });
      };
      cmd.append(pre, copy);
      body.append(cmd);

      if (e.query.length) {
        const q = document.createElement('div');
        q.className = 'params';
        q.innerHTML = '<b>Query parameters:</b> ' +
          e.query.map(p => '<span>' + p + '</span>').join('');
        body.append(q);
      }

      ep.append(head, body);
      list.append(ep);
    });
  });

  document.getElementById('empty').hidden = shown > 0;
}

loadVars();
renderVars();

const search = document.createElement('div');
search.className = 'field';
search.innerHTML = '<label>Filter</label><input id="search" placeholder="shipment, POST, status…">';
document.getElementById('vars').append(search);
document.getElementById('search').addEventListener('input', renderList);

renderList();
</script>
</body>
</html>
"""
