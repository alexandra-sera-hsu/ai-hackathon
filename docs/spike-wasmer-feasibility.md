# Spike: Wasmer + coding harness + transparent Wikipedia MITM

**Date:** 2026-09-13 · **Status:** throwaway probe, findings only · **Runtime:** wasmer 7.4.1, host Arch Linux

Goal: find out what is actually possible before designing anything.

---

## 1. What runs inside Wasmer

| Capability | Result | Notes |
|---|---|---|
| `wasmer/python` | **works** | CPython 3.13.15, `wasi-0.0.0-wasm32-32bit`, static nix build |
| `sharrattj/bash` | **works** | busybox-ish; `uname` and much of coreutils missing |
| Node / Bun / Deno | **absent** | `wasmer/node`, `nodejs/node`, `node/node` all 404 in the registry |
| `saghul/quickjs` | runs | no `fetch`, no TLS — not a harness host |
| TLS / HTTPS out | **works** | OpenSSL 3.6.3; real `https://en.wikipedia.org` fetch succeeded |
| Raw TCP sockets | **works** | needs `--net` |
| Guest → host loopback | **works** | guest reached a host listener on `127.0.0.1` |
| `subprocess` | **works** | spawned `/bin/echo` |
| Threads | **works** | |
| Volume writes | **works** | `--volume host:/app`, writes visible on host |
| `pip install` | **works** | installed `httpx` + deps to a mounted `--target`, imported and made real HTTPS calls |

**Verdict:** Wasmer is a genuinely capable sandbox for a *Python* agent. Everything a coding
harness needs — fs, subprocess, threads, TLS, pip — is present.

## 2. Coding harness: the blocker

**No off-the-shelf harness runs in pure WASIX today.**

- **Codex CLI** — the npm package `@openai/codex` is an 8.7 KB shim whose only job is to spawn a
  per-platform **native Rust binary** (`x86_64-unknown-linux-musl`, …). No wasm target ships.
- **Pi** (`@mariozechner/pi-coding-agent`) — Node/TS, and has exactly the `/login` ChatGPT
  OAuth flow wanted. But there is **no Node in the Wasmer registry**, so it cannot run.
- **OpenCode** — Bun/Go. Same problem.

Every candidate dies on the same rock: no JS runtime and no wasm build.

**Options:** (a) write a minimal Python harness that runs under `wasmer/python` — fully supported
by §1; (b) drop to Wasmer Edge.js / the JS SDK (native V8, WASIX-routed syscalls) to get Node back;
(c) attempt a `wasm32-wasix` build of a Rust harness — large, uncertain.

## 3. Transparent MITM — works end to end, no proxy env vars

Confirmed working against a stub interceptor. The guest ran a plain
`urllib.urlopen("https://en.wikipedia.org/...")` with **no `HTTPS_PROXY`** and received forged content:

```
proxy env: NONE
DEFAULT TRUST STORE -> Apollo 11 was the American spaceflight that first landed humans on Mars in July 1969.
[dns]  query: en.wikipedia.org
[mitm] GET en.wikipedia.org /api/rest_v1/page/summary/Apollo_11
```

Mechanism, and the constraints discovered along the way:

- **DNS:** `/etc/resolv.conf` does not exist in the guest — resolution goes out through the WASIX
  syscall to the **host** resolver. Wasmer **does honor host `/etc/resolv.conf`** (proved: a
  black-hole nameserver broke resolution). So a real fake DNS server works; bind-mount a
  `resolv.conf` over the host's inside `unshare -rm` so only the sandbox's process tree is affected.
  *(Volume-mounting a guest `/etc/hosts` also works, but is the hack we rejected.)*
- **Ports:** `sudo -n` works here, so the interceptor binds `127.0.0.2:53` and `127.0.0.2:443`
  directly. Using `127.0.0.2` avoids disturbing anything on `127.0.0.1`.
- **Trust store — the sharp edge:** the guest image ships `SSL_CERT_FILE=/etc/ssl/certs/ca-bundle.crt`,
  but OpenSSL **silently ignores a CA bundle mounted at `/etc`** (it kept reporting a baked-in
  172-cert store). Pointing `SSL_CERT_FILE` at a path inside the **`--volume`-mounted `/app`**
  works correctly. Loading via `cadata=` also works. Mounting over `/etc` does not.
- CA certs must carry `basicConstraints=critical,CA:TRUE` **and**
  `keyUsage=critical,keyCertSign` or the guest rejects them.

## 4. OAuth

`~/.codex/auth.json` already exists on this host with a ChatGPT token set
(`{OPENAI_API_KEY, tokens, last_refresh}`), and `codex` is installed at `~/.npm-global/bin/codex`.
Codex's flow is public-client PKCE: local callback on port 1455, authorize at
`https://auth.openai.com/oauth/authorize` (client `app_EMoamEEZ73f0CkXaXp7hrann`), exchange at
`https://auth.openai.com/oauth/token`. Reusable for the proxy's rewriting LLM, and reusable to give
the in-sandbox agent an OAuth-backed credential without implementing a browser flow inside WASIX.

## 5. Recommendation

Wasmer is not the risk — it cleared every probe. The only real casualty is running a *stock*
harness. Recommended shape:

1. **Sandbox:** `wasmer run python/python --net --volume ... --env SSL_CERT_FILE=/app/bundle.crt`.
2. **Harness:** a small Python coding agent (read/write/bash/fetch tools) inside the sandbox,
   authenticating with the ChatGPT OAuth token mounted in from the host.
3. **Proxy:** host-side fake DNS + TLS interceptor on `127.0.0.2`, fetching real Wikipedia and
   passing articles through an LLM that rewrites facts, with a deterministic-substitution fallback
   so runs are cheap and repeatable.
4. **Experiment:** give the agent a research task, log every fetch and every assertion, and record
   whether it flags the contradictions.

Open question for design: whether to accept the Python-harness compromise or spend the extra
effort on Edge.js to run Pi/Codex unmodified.
