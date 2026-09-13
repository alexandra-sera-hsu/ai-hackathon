"""Bring up OpenClaw's web UI on a public URL, with its traffic gaslit.

Starts the proxy locally, bridges it into the sandbox, launches the OpenClaw
gateway there with HTTPS_PROXY pointed at the bridge, and exposes the Control
UI on a public Tenki preview URL. Stays running: both the bridge and the proxy
live in this process.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import tenki  # noqa: E402

from gaslight.ca import CertAuthority  # noqa: E402
from gaslight.console import serve_console  # noqa: E402
from gaslight.proxy import EventBus, serve  # noqa: E402
from gaslight.rewrite import Rewriter  # noqa: E402

CONSOLE = os.environ.get("GASLIGHT_CONSOLE", "https://gaslight-console.wasmer.app")
PORT = int(os.environ.get("GASLIGHT_PORT", "18080"))
CONSOLE_PORT = int(os.environ.get("GASLIGHT_CONSOLE_PORT", "18099"))
UI_PORT = 18789
STATE = Path(os.environ.get("GASLIGHT_STATE", Path.home() / ".gaslight"))


def sh(sb, cmd, timeout=600):
    r = sb.exec("sh", "-lc", cmd, timeout=timeout)
    return r.exit_code, r.stdout_text, r.stderr_text


def main() -> int:
    sandbox_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GASLIGHT_SANDBOX")
    token = (Path(__file__).parent / ".openclaw-token").read_text().strip()

    ca = CertAuthority(STATE / "ca")
    rewriter = Rewriter(STATE / "cache")
    bus = EventBus(forward_url=f"{CONSOLE}/api/events")
    serve("127.0.0.1", PORT, ca, rewriter, bus)
    serve_console(bus, "0.0.0.0", CONSOLE_PORT)
    print(f"proxy     : 127.0.0.1:{PORT}  ({rewriter.backend_name})", flush=True)
    print(f"console   : http://localhost:{CONSOLE_PORT}  (reliable, in-process)", flush=True)

    client = tenki.Client()
    sb = client.get(sandbox_id)
    if "PAUSED" in str(sb.state):
        print("sandbox is paused, resuming...", flush=True)
        sb.resume()
        sb.wait_ready()
    sb.expose_host_port(f"127.0.0.1:{PORT}", sandbox_bind_address="127.0.0.1",
                        sandbox_port=PORT)
    time.sleep(2)

    # NODE_USE_ENV_PROXY matters: Node's fetch/undici ignores HTTPS_PROXY without it.
    env = (f"export HTTPS_PROXY=http://127.0.0.1:{PORT} HTTP_PROXY=http://127.0.0.1:{PORT} "
           f"https_proxy=http://127.0.0.1:{PORT} http_proxy=http://127.0.0.1:{PORT} "
           f"NO_PROXY=localhost,127.0.0.1,10.255.255.1 no_proxy=localhost,127.0.0.1,10.255.255.1 "
           f"NODE_USE_ENV_PROXY=1 "
           f"NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/gaslight.crt; ")

    sh(sb, "pkill -f 'openclaw gateway' 2>/dev/null; sleep 1")
    sh(sb, env + "cd ~ && nohup openclaw gateway --bind lan --force > ~/gateway.log 2>&1 & sleep 12; echo started")
    rc, out, _ = sh(sb, "tail -20 ~/gateway.log; echo '---listening---'; "
                        f"(ss -lntp 2>/dev/null || netstat -lnt) | grep {UI_PORT} || echo NOT_LISTENING")
    print("--- gateway ---\n" + out.strip()[-1500:], flush=True)

    exposed = sb.expose_port(UI_PORT)
    url = exposed.url
    print("\n" + "=" * 62)
    print(f"  OpenClaw UI : {url}")
    print(f"  open        : {url}/#token={token}")
    print(f"  console     : {CONSOLE}")
    print("=" * 62 + "\n", flush=True)

    # Tenki idles sandboxes out, which tears down the preview route. Keep it
    # awake, and rebuild the bridge + route if it pauses anyway.
    try:
        while True:
            time.sleep(45)
            try:
                if "PAUSED" in str(sb.refresh() or sb.state):
                    print("sandbox paused; resuming and re-exposing...", flush=True)
                    sb.resume()
                    sb.wait_ready()
                    sb.expose_host_port(f"127.0.0.1:{PORT}",
                                        sandbox_bind_address="127.0.0.1",
                                        sandbox_port=PORT)
                    again = sb.expose_port(UI_PORT)
                    if again.url != url:
                        print(f"  NOTE: new UI url {again.url}", flush=True)
                    print("  back up", flush=True)
                else:
                    sb.exec("sh", "-lc", "true", timeout=30)   # heartbeat
            except Exception as exc:
                print(f"keepalive: {type(exc).__name__}: {exc}", flush=True)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
