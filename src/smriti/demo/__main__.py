"""SMRITI demo: vendor bank-change fraud against two accounts-payable agents.

    python -m smriti.demo                 # serve the demo at http://localhost:8765
                                          #   /           live test: write the email yourself
                                          #   /story.html the recorded walkthrough
    python -m smriti.demo --export        # write static/trace.js (offline trace) and exit
    python -m smriti.demo --live --export # same, with Nemotron (tunnel to :8000 open)

The page opens on the recorded offline trace; when served locally it also offers a live
re-run on Nemotron (needs the agent LLM tunnel; hosted embeddings/judge if NVIDIA_API_KEY).
"""
import argparse
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from smriti.demo import playground
from smriti.demo.engine import live_parts, offline_parts, run, tally

STATIC = Path(__file__).parent / "static"
ROOT = Path(__file__).resolve().parents[3]


def export(trace: dict, path: Path = STATIC / "trace.js") -> Path:
    path.write_text("window.SMRITI_TRACE = " + json.dumps(trace, indent=1) + ";\n")
    return path


class _Handler(SimpleHTTPRequestHandler):
    live_args: tuple[str, str] = ("", "")
    lock = threading.Lock()
    cache: dict[str, dict] = {}

    def log_message(self, fmt, *args):
        pass

    jobs = playground.Jobs()

    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))
                                 or b"{}")
            if path == "/api/run":
                return self._json({"id": self.jobs.start(payload).id})
            parts = path.strip("/").split("/")          # api/job/<id>/confirm
            if len(parts) == 4 and parts[:2] == ["api", "job"] and parts[3] == "confirm":
                job = self.jobs.get(parts[2])
                ok = job is not None and job.answer(str(payload.get("arm")),
                                                    bool(payload.get("approved")))
                return self._json({"ok": ok}, 200 if ok else 404)
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/presets":
            return self._json({
                "presets": playground.PRESETS, "seed": list(playground.SEED_MEMORIES),
                "ready": bool(os.environ.get("NVIDIA_API_KEY")),
                "models": [playground.HOSTED_MODEL + " (agent)",
                           "Nemotron Safety Guard 8B", "nemotron-3.5-embed-1b", "Presidio"]})
        if url.path.startswith("/api/job/"):
            job = self.jobs.get(url.path.rsplit("/", 1)[-1])
            return self._json(job.state() if job else {"error": "no such run"},
                              200 if job else 404)
        if url.path != "/trace.json":
            return super().do_GET()
        mode = parse_qs(url.query).get("mode", ["offline"])[0]
        try:
            with self.lock:                       # one run at a time; the model is shared
                if mode not in self.cache or mode == "live":
                    parts = live_parts(*self.live_args) if mode == "live" else offline_parts()
                    self.cache[mode] = run(parts)
            body, status = json.dumps(self.cache[mode]).encode(), 200
        except Exception as e:
            body, status = json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), 503
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--export", action="store_true", help="write static/trace.js and exit")
    ap.add_argument("--live", action="store_true", help="use Nemotron for --export")
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="nvidia/nvidia-nemotron-nano-9b-v2")
    ap.add_argument("--out", type=Path, default=STATIC / "trace.js", help="--export target")
    ap.add_argument("--runs", type=int, default=0,
                    help="with --live: also tally N sampled runs (temperature 0.7)")
    args = ap.parse_args(argv)

    from smriti.bench.run import load_dotenv
    load_dotenv(ROOT / ".env")
    if args.export:
        parts = live_parts(args.base_url, args.model) if args.live else offline_parts()
        trace = run(parts)
        if args.live and args.runs:
            trace["tally"] = tally(args.base_url, args.model, args.runs)
            print(json.dumps(trace["tally"], indent=2))
        print(json.dumps(trace["verdict"], indent=2))
        print(f"wrote {export(trace, args.out)}")
        return
    if not (STATIC / "trace.js").exists():
        export(run(offline_parts()))
    _Handler.live_args = (args.base_url, args.model)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(_Handler, directory=str(STATIC)))
    print(f"SMRITI demo at http://localhost:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
