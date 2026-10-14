"""Exercise public BAT entry points using isolated state and a different cwd."""
import json
import os
from pathlib import Path
import socket
import subprocess
from uuid import uuid4

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != "nt":
        raise SystemExit("This acceptance check requires Windows")
    run = ROOT / "artifacts/tests/windows_launchers" / uuid4().hex[:12]
    cwd = run / "other working directory"
    cwd.mkdir(parents=True)
    state = run / "state"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCREEN_GUARD_")}
    env.update(SCREEN_GUARD_PYTHON=os.sys.executable, SCREEN_GUARD_DATA_DIR=str(run / "data"),
               SCREEN_GUARD_STATE_DIR=str(state), SCREEN_GUARD_AUTH_MODE="local",
               PYTHONDONTWRITEBYTECODE="1", TEMP=str(run), TMP=str(run), TMPDIR=str(run))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    results = []
    process = None

    def command(name, *args):
        # Detached Windows children can inherit pipe handles; use files and NUL.
        log_path = run / f"{len(results)}-{name}.log"
        with log_path.open("wb") as log:
            result = subprocess.run(["cmd.exe", "/d", "/c", str(ROOT / name), *args], cwd=cwd, env=env,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=60)
        results.append({"entry": name, "returncode": result.returncode})
        assert result.returncode == 0, name + " failed; inspect " + str(run)

    try:
        command("start_windows.bat", "-NoBrowser", "-Port", str(port))
        saved = json.loads((state / "server_state.json").read_text(encoding="utf-8-sig"))
        process = psutil.Process(saved["pid"])
        assert str(ROOT / "scripts/serve_windows.py").lower() in [x.lower() for x in process.cmdline()]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
            client.get("/api/bootstrap").raise_for_status()
            client.get("/api/health").raise_for_status()
            assert client.get("/api/sources").json() == []
            assert client.get("/api/preview").json()["pid"] == process.pid
            for route in ("/", "/static/app.js", "/static/runtime-ui.js", "/api/runtime/status"):
                client.get(route).raise_for_status()
            command("start_windows.bat", "-NoBrowser", "-Port", str(port))
            assert client.get("/api/preview").json()["pid"] == process.pid
        command("stop_windows.bat")
        process.wait(timeout=15)
        command("stop_windows.bat")
        command("stop_local_model.bat")
        assert (run / "data/screen_guard.db").is_file()
        report = {"passed": True, "different_cwd": True, "isolated_runtime": True, "repeated_start_same_pid": True,
                  "clean_shutdown": True, "entries": results, "run_directory": str(run)}
        (run / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        if process is None and (state / "server_state.json").exists():
            saved = json.loads((state / "server_state.json").read_text(encoding="utf-8-sig"))
            try:
                candidate = psutil.Process(saved["pid"])
                if str(ROOT / "scripts/serve_windows.py").lower() in [x.lower() for x in candidate.cmdline()]:
                    process = candidate
            except psutil.NoSuchProcess:
                pass
        if process is not None and process.is_running():
            children = process.children(recursive=True)
            process.terminate()
            process.wait(timeout=10)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(children, timeout=10)


if __name__ == "__main__":
    main()
