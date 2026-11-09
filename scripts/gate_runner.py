"""Local job runner for assisted gate runs. Start it with scripts\\start_runner.cmd.

It watches outputs/gates/queue/ for job files (JSON) and runs them one at a time:

    {"id": "04-run-mitte", "argv": ["leadgraph", "run", "--city", "berlin"],
     "kill_after_s": 90, "stdin": "1,3\\n\\n"}

Only these commands are executed, without a shell:
    uv run leadgraph <args> · uv run pytest <args> · uv sync --all-extras
Output goes to outputs/gates/logs/<id>.log, progress to outputs/gates/status.json.
Close the window (or press Ctrl+C) to stop it. Nothing else on the machine is touched.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES = ROOT / "outputs" / "gates"
QUEUE, LOGS, DONE = GATES / "queue", GATES / "logs", GATES / "done"
STATUS = GATES / "status.json"
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
history: list[dict] = []


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_status(current: dict | None) -> None:
    STATUS.write_text(json.dumps({"alive": now(), "pid": os.getpid(), "current": current,
                                  "history": history[-50:]}, indent=2), encoding="utf-8")


def command_for(argv: list[str]) -> list[str]:
    if not argv or not all(isinstance(a, str) and "\n" not in a for a in argv):
        raise ValueError("argv must be a non-empty list of single-line strings")
    if argv[0] in ("leadgraph", "pytest"):
        return ["uv", "run", *argv]
    if argv == ["sync"]:
        return ["uv", "sync", "--all-extras"]
    raise ValueError(f"command not allowed: {argv[0]}")


def kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def run_job(path: Path) -> None:
    job = json.loads(path.read_text(encoding="utf-8"))
    job_id = str(job.get("id") or path.stem)
    if not SAFE_ID.match(job_id):
        job_id = path.stem
    record = {"id": job_id, "argv": job.get("argv"), "started": now()}
    path.replace(DONE / path.name)
    log = LOGS / f"{job_id}.log"
    try:
        cmd = command_for(job.get("argv") or [])
    except ValueError as exc:
        log.write_text(f"REFUSED: {exc}\n", encoding="utf-8")
        history.append({**record, "exit": "refused", "error": str(exc)})
        return
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "NO_COLOR": "1", "COLUMNS": "160"}
    write_status(record)
    t0 = time.monotonic()
    with log.open("w", encoding="utf-8") as out:
        out.write(f"$ {' '.join(cmd)}\n")
        out.flush()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.PIPE if job.get("stdin") else
                                subprocess.DEVNULL, text=True)
        if job.get("stdin"):
            proc.stdin.write(job["stdin"])
            proc.stdin.close()
        killed = False
        while proc.poll() is None:
            time.sleep(1)
            if job.get("kill_after_s") and time.monotonic() - t0 > float(job["kill_after_s"]):
                kill_tree(proc)
                killed = True
                break
            if int(time.monotonic() - t0) % 15 == 0:
                write_status({**record, "running_s": int(time.monotonic() - t0)})
        code = proc.wait()
    history.append({**record, "exit": "killed" if killed else code,
                    "duration_s": round(time.monotonic() - t0, 1), "finished": now()})


def main() -> None:
    for d in (QUEUE, LOGS, DONE):
        d.mkdir(parents=True, exist_ok=True)
    print(f"LeadGraph gate runner watching {QUEUE}\nClose this window to stop.")
    while True:
        jobs = sorted(QUEUE.glob("*.json"))
        if jobs:
            print(f"[{now()}] running {jobs[0].name}")
            try:
                run_job(jobs[0])
            except Exception as exc:  # keep the runner alive
                history.append({"id": jobs[0].stem, "exit": "runner_error", "error": str(exc)})
                if jobs[0].exists():
                    jobs[0].replace(DONE / jobs[0].name)
            print(f"[{now()}] finished {jobs[0].name}: {history[-1].get('exit')}")
        write_status(None)
        time.sleep(3)


if __name__ == "__main__":
    main()
