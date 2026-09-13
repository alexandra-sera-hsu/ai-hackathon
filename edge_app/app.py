"""Gaslight console: receives intercept events and renders a live MITM view.

Runs anywhere a WASIX/CPython runtime does -- locally via `wasmer run`, or
deployed to Wasmer Edge. Deliberately dependency-free (stdlib only) so the
same file works in all three places.
"""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_EVENTS = 300
EVENTS: deque[dict] = deque(maxlen=MAX_EVENTS)
LOCK = threading.Lock()

# Events are persisted to a Wasmer Edge volume (read-write-many, shared across
# instances) so the feed survives an instance recycling or a request landing on a
# different instance than the one that received it. In-memory is only a fallback
# for when no volume is mounted, e.g. running this file locally.
EVENTS_DIR = os.environ.get("EVENTS_DIR", "/data")
EVENTS_FILE = os.path.join(EVENTS_DIR, "events.jsonl")


def _store_ok() -> bool:
    try:
        os.makedirs(EVENTS_DIR, exist_ok=True)
        return os.access(EVENTS_DIR, os.W_OK)
    except Exception:
        return False


PERSIST = _store_ok()


def append_event(event: dict) -> int:
    """Append one event; returns the current count."""
    if PERSIST:
        try:
            # O_APPEND keeps concurrent single-line writes from interleaving.
            with open(EVENTS_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
            return len(read_events())
        except Exception:
            pass
    with LOCK:
        EVENTS.append(event)
        return len(EVENTS)


def read_events() -> list[dict]:
    """All retained events, newest last."""
    if PERSIST:
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as fh:
                lines = fh.readlines()[-MAX_EVENTS:]
            out = []
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
            return out
        except FileNotFoundError:
            return []
        except Exception:
            pass
    with LOCK:
        return list(EVENTS)

INDEX = r"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gaslight</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap" rel="stylesheet">
<style>
  :root{
    --ground:#F6F7F9; --panel:#FFFFFF; --ink:#1B1D21; --dim:#72777D;
    --rule:#C8CCD1; --source:#3366CC; --forged:#B31E8D; --forged-wash:#FBEAF5;
    --sans:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
    --serif:"Source Serif 4",Georgia,"Times New Roman",serif;
    --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  }
  @media (prefers-color-scheme:dark){
    :root{--ground:#14171C;--panel:#1B1F26;--ink:#E9ECF1;--dim:#8D949E;
          --rule:#2C323B;--source:#7AA7F0;--forged:#F06BC8;--forged-wash:#311A2B;}
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
       font-size:15px;line-height:1.5}

  .bar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:14px;
       padding:12px 20px;background:var(--panel);border-bottom:1px solid var(--rule);flex-wrap:wrap}
  .mark{font-family:var(--serif);font-size:19px;font-weight:600;letter-spacing:-.01em}
  .mark em{font-style:normal;color:var(--forged)}
  .state{font-size:13px;color:var(--dim)}
  .state b{font-weight:500;color:var(--ink)}
  .pulse{display:inline-block;width:7px;height:7px;border-radius:50%;
         background:var(--forged);margin-right:6px;vertical-align:middle}
  .live .pulse{animation:beat 1.6s ease-in-out infinite}
  @keyframes beat{0%,100%{opacity:1}50%{opacity:.25}}
  .ledger{margin-left:auto;display:flex;gap:22px;font-family:var(--mono);font-size:13px}
  .ledger div{color:var(--dim)}
  .ledger b{color:var(--ink);font-weight:500}

  main{max-width:1180px;margin:0 auto;padding:24px 20px 64px}

  .hero{background:var(--panel);border:1px solid var(--rule);border-radius:3px;
        padding:28px 30px;margin-bottom:26px}
  .src{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:18px;
       padding-bottom:14px;border-bottom:1px solid var(--rule)}
  .src a,.src span.u{font-family:var(--mono);font-size:13px;color:var(--source);
                     word-break:break-all;text-decoration:none}
  .src .t{font-family:var(--serif);font-size:26px;font-weight:600;line-height:1.2;
          width:100%;letter-spacing:-.015em}
  .tele{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--dim);white-space:nowrap}

  .prose{font-family:var(--serif);font-size:18.5px;line-height:1.62;max-width:68ch}
  .prose p{margin:0 0 .85em}
  .prose ins{text-decoration:none;background:var(--forged-wash);color:var(--forged);
             font-weight:600;padding:0 2px;border-radius:2px}
  .prose del{text-decoration:line-through;text-decoration-thickness:1px;
             color:var(--dim);opacity:.75;margin-right:4px;font-weight:400}
  .swap{transition:color .45s ease,background-color .45s ease}

  .note{margin-top:20px;padding-top:14px;border-top:1px solid var(--rule);
        font-size:13.5px;color:var(--dim)}
  .note b{color:var(--forged);font-weight:500}

  h2{font-size:14px;font-weight:600;margin:0 0 10px;color:var(--dim)}
  table{width:100%;border-collapse:collapse;background:var(--panel);
        border:1px solid var(--rule);border-radius:3px;overflow:hidden}
  th,td{text-align:left;padding:9px 14px;border-bottom:1px solid var(--rule);font-size:13.5px}
  th{font-weight:500;color:var(--dim);font-size:12.5px}
  tr:last-child td{border-bottom:none}
  td.u{font-family:var(--mono);font-size:12.5px;color:var(--source);
       max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  td.n{font-family:var(--mono);text-align:right;color:var(--dim)}
  td.t{font-family:var(--mono);font-size:12px;color:var(--dim);white-space:nowrap}
  .pill{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid currentColor}
  .pill.f{color:var(--forged);background:var(--forged-wash)}
  .pill.p{color:var(--dim)}
  tr.quiet td:not(.u){color:var(--dim)}
  tr.clk{cursor:pointer}
  tr.clk:hover td{background:var(--forged-wash)}
  tr.sel td{background:var(--forged-wash);box-shadow:inset 3px 0 0 var(--forged)}
  .pill.b{color:#b26a00;border-color:#b26a00;background:rgba(178,106,0,.12)}
  .tele a{color:var(--source);text-decoration:none}

  .empty{background:var(--panel);border:1px solid var(--rule);border-radius:3px;
         padding:56px 30px;text-align:center;color:var(--dim);max-width:46ch;margin:0 auto}
  .empty p{margin:0 0 6px}
  .empty code{font-family:var(--mono);font-size:13px;color:var(--ink)}
  a:focus-visible,tr:focus-visible{outline:2px solid var(--source);outline-offset:2px}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
  @media (max-width:640px){
    main{padding:16px 14px 48px}.hero{padding:20px 18px}
    .prose{font-size:17px}.src .t{font-size:22px}.ledger{width:100%;margin-left:0;gap:16px}
  }
</style>

<div class="bar" id="bar">
  <span class="mark">gaslight<em>.</em></span>
  <span class="state"><span class="pulse"></span><b id="st">idle</b></span>
  <div class="ledger">
    <div>articles <b id="k-art">0</b></div>
    <div>claims rewritten <b id="k-clm">0</b></div>
    <div>passed through <b id="k-pass">0</b></div>
    <div>last <b id="k-last">never</b></div>
  </div>
</div>

<main>
  <div id="hero"></div>
  <section id="log-wrap" hidden>
    <h2>Everything the agent has fetched</h2>
    <table><thead><tr><th>Time</th><th>Request</th><th>Handling</th><th>Source</th>
      <th style="text-align:right">Claims</th></tr></thead><tbody id="log"></tbody></table>
  </section>
</main>

<script>
const esc = s => (s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const clock = iso => { const d = new Date(iso); return isNaN(d) ? "--:--:--"
  : d.toLocaleTimeString([], {hour12:false, hour:'2-digit', minute:'2-digit', second:'2-digit'}); };
function ago(iso){
  const d = new Date(iso); if(isNaN(d)) return "";
  const s = Math.max(0, (Date.now() - d.getTime())/1000);
  if(s < 10) return "just now";
  if(s < 60) return Math.floor(s) + "s ago";
  if(s < 3600) return Math.floor(s/60) + "m ago";
  if(s < 86400) return Math.floor(s/3600) + "h ago";
  return Math.floor(s/86400) + "d ago";
}
const sentences = t => (t||"").split(/(?<=[.!?])\s+/).filter(Boolean);
const words = t => (t||"").split(/(\s+)/);

// Word-level LCS so we can show precisely which words were swapped.
function diffWords(a, b){
  const A = words(a), B = words(b);
  const m = Array.from({length:A.length+1}, () => new Uint32Array(B.length+1));
  for(let i=A.length-1;i>=0;i--)
    for(let j=B.length-1;j>=0;j--)
      m[i][j] = A[i]===B[j] ? m[i+1][j+1]+1 : Math.max(m[i+1][j], m[i][j+1]);
  let i=0,j=0,out=[],delBuf=[],insBuf=[];
  const flush = () => {
    if(delBuf.length) out.push(['del', delBuf.join('')]);
    if(insBuf.length) out.push(['ins', insBuf.join('')]);
    delBuf=[]; insBuf=[];
  };
  while(i<A.length && j<B.length){
    if(A[i]===B[j]){ flush(); out.push(['same',A[i]]); i++; j++; }
    else if(m[i+1][j] >= m[i][j+1]) delBuf.push(A[i++]);
    else insBuf.push(B[j++]);
  }
  while(i<A.length) delBuf.push(A[i++]);
  while(j<B.length) insBuf.push(B[j++]);
  flush();
  return out;
}

function renderProse(original, gaslit){
  const A = sentences(original), B = sentences(gaslit);
  let flipped = 0;
  const html = B.map((s,i) => {
    const a = A[i];
    if(a === undefined || a.trim() === s.trim()) return esc(s);
    flipped++;
    return diffWords(a, s).map(([k,t]) =>
      k==='same' ? esc(t)
      : k==='del' ? `<del>${esc(t)}</del>`
      : `<ins class="swap">${esc(t)}</ins>`).join('');
  }).join(' ');
  return {html, flipped};
}

let lastSeq = -1;
let selectedSeq = null;  // pinned log row, or null = follow latest
async function poll(){
  let evs;
  try { evs = await (await fetch('/api/events')).json(); } catch(e){ return; }

  const forged = evs.filter(e => e.kind === 'gaslit');
  const passed = evs.filter(e => e.kind === 'passthrough' || e.kind === 'untouched');
  document.getElementById('k-art').textContent = forged.length;
  document.getElementById('k-clm').textContent =
    forged.reduce((n,e) => n + ((e.changes||[]).length), 0);
  document.getElementById('k-pass').textContent = passed.length;
  const newestAny = evs[evs.length-1];
  const lastEl = document.getElementById('k-last');
  lastEl.textContent = newestAny ? ago(newestAny.at) : 'never';
  if(newestAny) lastEl.dataset.at = newestAny.at;

  const newest = evs[evs.length-1];
  const fresh = newest && newest.seq !== lastSeq;
  if(fresh){
    lastSeq = newest.seq;
    document.getElementById('bar').classList.add('live');
    document.getElementById('st').textContent =
      newest.kind === 'gaslit' ? 'rewriting ' + (newest.title || '') : 'passing traffic through';
    clearTimeout(window.__idle);
    window.__idle = setTimeout(() => {
      document.getElementById('bar').classList.remove('live');
      document.getElementById('st').textContent = 'idle';
    }, 2500);
  }

  // Keep every gaslit event addressable by seq so a log click can recall it.
  window.__bySeq = {};
  for(const e of forged) window.__bySeq[e.seq] = e;

  // Pin to a clicked row if it's still a gaslit event; otherwise follow the latest.
  let shown = (selectedSeq != null) ? window.__bySeq[selectedSeq] : null;
  if(!shown) shown = forged[forged.length-1];

  renderHero(shown);

  const wrap = document.getElementById('log-wrap');
  wrap.hidden = evs.length === 0;
  document.getElementById('log').innerHTML = evs.slice().reverse().slice(0,60).map(e => {
    const forgedRow = e.kind === 'gaslit';
    const sel = (shown && e.seq === shown.seq) ? ' sel' : '';
    const click = forgedRow ? ` onclick="pick(${e.seq})"` : '';
    const label = e.kind === 'blocked' ? 'blocked' : (forgedRow ? 'rewritten' : 'untouched');
    const pill = e.kind === 'blocked' ? 'b' : (forgedRow ? 'f' : 'p');
    return `<tr class="${forgedRow?'clk':'quiet'}${sel}"${click}>
      <td class="t" title="${esc(e.at||'')}">${clock(e.at)}</td>
      <td class="u">${esc(e.url || e.host || '')}</td>
      <td><span class="pill ${pill}">${label}</span></td>
      <td>${esc(forgedRow ? (e.backend||'') : '')}</td>
      <td class="n">${forgedRow ? ((e.changes||[]).length || '') : ''}</td></tr>`;
  }).join('');
}

function renderHero(ev){
  const hero = document.getElementById('hero');
  if(!ev){
    hero.innerHTML = `<div class="empty"><p>No traffic yet.</p>
      <p>Send an agent to Wikipedia through the proxy and its reading will appear here,
         rewritten in transit. Click any rewritten row below to inspect it.</p></div>`;
    return;
  }
  const {html, flipped} = renderProse(ev.original_excerpt, ev.gaslit_excerpt);
  const pinned = (selectedSeq != null);
  hero.innerHTML = `<article class="hero">
    <div class="src">
      <span class="t">${esc(ev.title || 'Untitled')}</span>
      <span class="u">${esc(ev.url)}</span>
      <span class="tele">${clock(ev.at)} &middot; ${ago(ev.at)}${
          ev.backend ? ' &middot; ' + esc(ev.backend) : ''}${
          ev.cached ? ' &middot; from cache' : ''}${
          pinned ? ' &middot; <a href="#" onclick="pick(null);return false">unpin</a>' : ''}</span>
    </div>
    <div class="prose"><p>${html}</p></div>
    <div class="note">${flipped
      ? `<b>${flipped}</b> ${flipped===1?'sentence':'sentences'} altered before the agent read this.
         Struck text is what Wikipedia actually says.`
      : 'Served to the agent as shown.'}</div>
  </article>`;
}

function pick(seq){
  selectedSeq = seq;
  if(seq != null && window.__bySeq && window.__bySeq[seq]) renderHero(window.__bySeq[seq]);
  poll();
}
poll(); setInterval(poll, 1100);
// refresh relative times between polls so "last" never looks frozen
setInterval(() => {
  const el = document.getElementById('k-last');
  if(el && el.dataset.at) el.textContent = ago(el.dataset.at);
}, 1000);
</script>
"""




class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/events"):
            self._send(200, json.dumps(read_events()))
        elif self.path in ("/", "/index.html"):
            self._send(200, INDEX, "text/html; charset=utf-8")
        elif self.path == "/healthz":
            self._send(200, json.dumps({"ok": True, "events": len(read_events()),
                                        "persistent": PERSIST, "store": EVENTS_FILE}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self.path.startswith("/api/events"):
            return self._send(404, json.dumps({"error": "not found"}))
        n = int(self.headers.get("Content-Length") or 0)
        try:
            event = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, json.dumps({"error": "bad json"}))
        count = append_event(event)
        self._send(200, json.dumps({"ok": True, "count": count}))

    def log_message(self, *a):
        pass


def main():
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
