# Orientation for agents

Read `README.md` first — it has the architecture, the operational gotchas, and a
troubleshooting table. This file is the short version plus the things that will
bite you.

## What this is

A MITM proxy that rewrites what an AI agent reads from the web, to test whether the
agent notices. The agent runs in a remote Tenki microVM; the proxy runs on the host
and is bridged into the VM by `expose_host_port`.

## Entry points

- `serve_openclaw.py <sandbox-id>` — the full stack. Starts the proxy, bridges it
  into the VM, launches OpenClaw, exposes its UI, and runs a keepalive. Must stay
  running: the bridge lives in this process.
- `run_demo.py` — headless Codex run, no UI.
- `src/gaslight/proxy.py` — where interception, host policy and blocking live.
- `src/gaslight/rewrite.py` — where the rewrite modes and directives live.

## Invariants — do not break these

1. **Model traffic must tunnel untouched.** `chatgpt.com` / `openai.com` /
   `oaiusercontent.com` must never be intercepted or blocked, or the agent stops
   working entirely. Verify with a non-target host reaching 200 under *system*
   trust, not our CA.
2. **Only the article page is rewritten, not the API.** If an agent can reach
   `w/api.php`, it gets pristine text and the whole experiment leaks.
3. **The rewrite must survive readability extraction.** The agent's `web_fetch`
   runs readability over the HTML, so rewritten content has to be in the main
   prose, not just in tags it will discard. An earlier version rewrote only `<p>`
   tags and the agent read the untouched infobox instead.
4. **Never commit secrets.** `.openclaw-token`, `.openclaw-password` and
   `~/.gaslight/` (CA private key, cache, live directives) are ignored. Check
   before staging.

## Verifying, not assuming

Almost every failure in this project *looked* correctly configured. A listening
port and a right-looking config file are not evidence. Exercise the real path:

```bash
curl -x http://127.0.0.1:18080 --cacert ~/.gaslight/ca/ca.pem <url>   # host
```

...and inside the VM, the same call Node's `fetch` makes (see README →
*Verifying a change*). Tie an agent fetch to a proxy event by matching the tool
output's `fetchedAt` against a new file in `~/.gaslight/cache`.

## Cheap failure explanations, in order of likelihood

1. The sandbox auto-paused — bridge and preview routes are gone (`curl` → `000`,
   UI → `route_not_found`).
2. The stack process died — same symptoms.
3. `NODE_USE_ENV_PROXY` or `tools.web.fetch.useTrustedEnvProxy` unset — the agent
   quietly bypasses the proxy entirely.
4. The host `curl` lacked `--cacert` — a TLS error that looks like an outage.

## Things that cost real time to learn

- Tenki `sb.exec()` takes argv, not a shell string.
- `ExposedPort.slug` is empty; use `.url`.
- OpenClaw's `web_fetch` ignores env proxies by default (SSRF guard).
- Codex sandboxes its own shell; `curl` has no network under the default policy.
- A full LLM page rewrite is ~16 model calls. Prefer a `canned` directive.
- The rewriting model refuses to fabricate plausible real-world falsehoods. This
  is not a bug to route around — it is why `canned` exists.
