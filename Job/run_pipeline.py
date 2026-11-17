import json
import subprocess
import sys
import time
from pathlib import Path

TARGETS_PATH = Path("targets.json")
MILESTONE_LEVELS = [20, 40, 60, 80, 100]
FINISH_MARKERS = ["Processed:", "[SKIP] Job already processed. Skipping:"]
BAR_LENGTH = 20


def read_targets(path: Path):
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("targets.json must contain a JSON array of target objects.")

    return data


def format_eta(seconds: float) -> str:
    seconds = int(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def render_progress(completed: int, total: int) -> str:
    percent = int((completed / total) * 100)
    filled = int((percent / 100) * BAR_LENGTH)
    bar = "█" * filled + "░" * (BAR_LENGTH - filled)
    return f"[{bar}] {percent:3d}%"


def milestone_banner(level: int) -> str:
    messages = {
        20: "[>> ENCRYPTION BYPASSED <<]",
        40: "[>> MAINFRAME INFILTRATED <<]",
        60: "[>> AI PAYLOAD DEPLOYED <<]",
        80: "[>> TRACE ROUTES SCRAMBLED <<]",
        100: "[>> SYSTEM OVERRIDE COMPLETE <<]",
    }
    return messages.get(level, "")


def print_milestone(level: int):
    banner = milestone_banner(level)
    if not banner:
        return
    box = f"\n{banner}\n"
    print(box, flush=True)
    time.sleep(0.35)


def is_job_completion_line(line: str) -> bool:
    return any(marker in line for marker in FINISH_MARKERS)


def run_script(script_name, total_jobs=None, monitor_jobs=False):
    process = subprocess.Popen(
        [sys.executable, "-u", script_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process.stdout is not None
    completed = 0
    start_time = time.time()
    milestones = set()

    try:
        while True:
            raw_line = process.stdout.readline()
            if raw_line == "" and process.poll() is not None:
                break
            if not raw_line:
                continue

            line = raw_line.rstrip("\n")
            print(line, end="\n", flush=True)

            if monitor_jobs and is_job_completion_line(line):
                completed += 1
                elapsed = time.time() - start_time
                remaining = max(total_jobs - completed, 0)
                avg_time = elapsed / completed if completed else 0
                eta = format_eta(avg_time * remaining)
                progress = render_progress(completed, total_jobs)
                print(f"{progress}  ETA: {eta}", flush=True)

                percent = int((completed / total_jobs) * 100)
                for threshold in MILESTONE_LEVELS:
                    if percent >= threshold and threshold not in milestones:
                        milestones.add(threshold)
                        print_milestone(threshold)
                        break

    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        raise

    return process.wait()


def main():
    try:
        targets = read_targets(TARGETS_PATH)
    except json.JSONDecodeError:
        print("targets.json is invalid JSON. Please fix the payload file.")
        return
    except Exception as exc:
        print(f"ERROR: {exc}")
        return

    if not targets:
        print("targets.json is empty. Please inject URLs via the browser JS payload.")
        return

    total_jobs = len(targets)
    print(f"[INIT] Loaded {total_jobs} job(s) from targets.json")

    try:
        print("[1/4] Running batch_processor.py...")
        return_code = run_script("batch_processor.py", total_jobs=total_jobs, monitor_jobs=True)
        if return_code != 0:
            raise RuntimeError(f"batch_processor.py exited with code {return_code}")

        print("[2/4] Running organizer.py...")
        return_code = run_script("organizer.py")
        if return_code != 0:
            raise RuntimeError(f"organizer.py exited with code {return_code}")

        TARGETS_PATH.write_text("[]", encoding="utf-8")
        print("Pipeline Complete. targets.json cleared for the next batch.")

    except KeyboardInterrupt:
        print("\nPipeline interrupted by user. targets.json was not modified.")
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("Pipeline failed before cleanup. targets.json remains unchanged.")
        sys.exit(1)


if __name__ == "__main__":
    main()
