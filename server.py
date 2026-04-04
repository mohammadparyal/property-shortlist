#!/usr/bin/env python3
"""
SCRAPER CONTROL PANEL — Flask + SocketIO server.

Usage:
    python server.py              # Start on localhost:8080
    python server.py --port 9090  # Custom port

Opens a control panel in your browser where you can:
  - Toggle which communities to scrape
  - Add / edit / delete communities (saved to communities.json)
  - Start / stop scraping with real-time progress
  - Auto-retry on PF cookie errors (No __NEXT_DATA__)
  - Solve CAPTCHAs in the visible browser window
  - View live logs
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit

# ─── PATHS ────────────────────────────────────────────────────────────────────
BASE = os.path.abspath(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(BASE, "communities.json")
SCRIPTS = os.path.join(BASE, "scripts")

app = Flask(__name__, static_folder=BASE)
app.config["SECRET_KEY"] = "dubai-scraper-2026"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ─── GLOBAL STATE ─────────────────────────────────────────────────────────────
config_lock = threading.Lock()  # Protects communities.json reads/writes

scraper_state = {
    "running": False,
    "mode": "villa",          # villa | apartment | both
    "source": "all",          # all | pf | bayut
    "stop_requested": False,
    "start_time": None,
    "progress": {},           # {task_id: {status, listings, community, source, ...}}
    "total_scraped": 0,
    "total_deduped": 0,
    "errors": 0,
    "proc": None,             # subprocess.Popen reference for kill
}


# ─── CONFIG ENDPOINTS ─────────────────────────────────────────────────────────
def load_config():
    with config_lock:
        with open(CONFIG_PATH) as f:
            return json.load(f)

def save_config(data):
    with config_lock:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, CONFIG_PATH)  # atomic on most OSes


@app.route("/")
def index():
    return send_from_directory(BASE, "scraper_panel.html")


@app.route("/api/config")
def get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.json
    save_config(data)
    return jsonify({"ok": True})


@app.route("/api/community/add", methods=["POST"])
def add_community():
    """Add a new community to config."""
    data = request.json
    mode = data.get("mode", "villa")
    community = data.get("community")
    if not community or not community.get("name"):
        return jsonify({"error": "Name required"}), 400

    with config_lock:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        config[mode].append(community)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    return jsonify({"ok": True, "total": len(config[mode])})


@app.route("/api/community/update", methods=["POST"])
def update_community():
    """Update an existing community."""
    data = request.json
    mode = data.get("mode", "villa")
    idx = data.get("index")
    community = data.get("community")
    if idx is None or not community:
        return jsonify({"error": "Index and community data required"}), 400

    with config_lock:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        if 0 <= idx < len(config[mode]):
            config[mode][idx] = community
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp, CONFIG_PATH)
            return jsonify({"ok": True})
    return jsonify({"error": "Invalid index"}), 400


@app.route("/api/community/delete", methods=["POST"])
def delete_community():
    """Remove a community from config."""
    data = request.json
    mode = data.get("mode", "villa")
    idx = data.get("index")
    if idx is None:
        return jsonify({"error": "Index required"}), 400

    with config_lock:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        if 0 <= idx < len(config[mode]):
            removed = config[mode].pop(idx)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp, CONFIG_PATH)
            return jsonify({"ok": True, "removed": removed["name"]})
    return jsonify({"error": "Invalid index"}), 400


@app.route("/api/community/toggle", methods=["POST"])
def toggle_community():
    """Toggle enabled/disabled for a community."""
    data = request.json
    mode = data.get("mode", "villa")
    idx = data.get("index")
    if idx is None:
        return jsonify({"error": "Index required"}), 400

    with config_lock:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        if 0 <= idx < len(config[mode]):
            config[mode][idx]["enabled"] = not config[mode][idx].get("enabled", True)
            enabled = config[mode][idx]["enabled"]
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp, CONFIG_PATH)
            return jsonify({"ok": True, "enabled": enabled})
    return jsonify({"error": "Invalid index"}), 400


@app.route("/api/community/toggle-all", methods=["POST"])
def toggle_all_communities():
    """Batch toggle all communities for a mode. Single request instead of N."""
    data = request.json
    mode = data.get("mode", "villa")
    enabled = data.get("enabled", True)

    with config_lock:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        for c in config.get(mode, []):
            c["enabled"] = enabled
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    return jsonify({"ok": True, "mode": mode, "enabled": enabled, "count": len(config.get(mode, []))})


CAPTCHA_SIGNAL = os.path.join(BASE, ".captcha_signal")

@app.route("/api/captcha/continue", methods=["POST"])
def captcha_continue():
    """User clicked Continue after solving CAPTCHA. Signal the scraper to resume."""
    try:
        with open(CAPTCHA_SIGNAL, "w") as f:
            json.dump({"status": "continue", "time": time.time()}, f)
        emit_log("Continue signal sent — scraper will resume...", "ok")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/state")
def get_state():
    elapsed = 0
    if scraper_state["start_time"]:
        elapsed = int(time.time() - scraper_state["start_time"])
    return jsonify({k: v for k, v in scraper_state.items() if k != "proc"}, elapsed=elapsed)


# ─── SCRAPER ENGINE ───────────────────────────────────────────────────────────
def emit_log(msg, level="info"):
    """Send a log line to all connected clients."""
    ts = datetime.now().strftime("%H:%M:%S")
    socketio.emit("log", {"time": ts, "msg": msg, "level": level})


def emit_progress(task_id, data):
    """Update progress for one scrape task."""
    scraper_state["progress"][task_id] = data
    socketio.emit("progress", {"task_id": task_id, **data})


def emit_alert(alert_type, msg, task_id=None):
    """Send an alert banner to the UI."""
    socketio.emit("alert", {"type": alert_type, "msg": msg, "task_id": task_id})


def emit_stats():
    """Send updated overall stats."""
    elapsed = int(time.time() - scraper_state["start_time"]) if scraper_state["start_time"] else 0
    socketio.emit("stats", {
        "total_scraped": scraper_state["total_scraped"],
        "total_deduped": scraper_state["total_deduped"],
        "errors": scraper_state["errors"],
        "elapsed": elapsed,
    })


def run_scraper_thread(mode, source, selected_communities=None):
    """Run the actual scraper in a background thread using subprocess.

    The scrapers now support --config flag to read from communities.json,
    and --no-process to skip the post-processor (server runs it separately).
    """
    scraper_state["running"] = True
    scraper_state["stop_requested"] = False
    scraper_state["start_time"] = time.time()
    scraper_state["progress"] = {}
    scraper_state["total_scraped"] = 0
    scraper_state["total_deduped"] = 0
    scraper_state["errors"] = 0
    scraper_state["proc"] = None

    config = load_config()
    modes_to_run = []
    if mode in ("villa", "both"):
        modes_to_run.append("villa")
    if mode in ("apartment", "both"):
        modes_to_run.append("apartment")

    for m in modes_to_run:
        if scraper_state["stop_requested"]:
            break

        communities = config.get(m, [])
        # Filter to enabled + selected
        communities = [c for c in communities if c.get("enabled", True)]
        if selected_communities:
            communities = [c for c in communities if c["name"] in selected_communities]

        if not communities:
            emit_log(f"No enabled {m} communities to scrape", "warn")
            continue

        script = "auto_scrape.py" if m == "villa" else "auto_scrape_apartments.py"
        script_path = os.path.join(SCRIPTS, script)

        # Build command — use --config to read from communities.json
        # and --no-process to skip the built-in process_deals.py step
        cmd = [sys.executable, "-u", script_path, "--visible", "--config", CONFIG_PATH, "--no-process"]
        if source == "pf":
            cmd.append("--pf-only")
        elif source == "bayut":
            cmd.append("--bayut-only")

        emit_log(f"Starting {m} scraper: {script}", "info")
        emit_log(f"  Communities: {', '.join(c['name'] for c in communities)}", "info")

        # Initialize progress entries for each community
        for c in communities:
            if source != "bayut":
                # At least one PF entry per community
                tid = f"{m}_{c['name']}_pf"
                emit_progress(tid, {
                    "community": c["name"], "source": "PF", "mode": m,
                    "status": "queued", "listings": 0, "time": "—"
                })
            if source != "pf":
                # At least one Bayut entry per community
                bayut_entries = c.get("bayut", [])
                if bayut_entries:
                    for i, entry in enumerate(bayut_entries):
                        tid = f"{m}_{c['name']}_bayut_{i}"
                        emit_progress(tid, {
                            "community": c["name"], "source": "Bayut", "mode": m,
                            "status": "queued", "listings": 0, "time": "—",
                            "prop_type": entry.get("prop_type", "")
                        })

        # Run the scraper as a subprocess with piped output
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=BASE,
                preexec_fn=os.setsid  # Create new process group for clean kill
            )
            scraper_state["proc"] = proc

            current_community = None
            current_source = None

            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue

                if scraper_state["stop_requested"]:
                    # Kill the entire process group (Playwright + browser)
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        proc.terminate()
                    emit_log("Scraper stopped by user", "warn")
                    break

                # Parse log line for progress updates
                # Strip timestamp prefix like [14:32:01]
                clean = line
                if clean.startswith("[") and "]" in clean:
                    clean = clean[clean.index("]") + 1:].strip()

                # Detect community start: "PF: DAMAC Lagoons" or "Bayut: DAMAC Lagoons"
                pf_match = re.match(r'^PF:\s+(.+)$', clean)
                bayut_match = re.match(r'^Bayut:\s+(.+)$', clean)

                if pf_match:
                    current_source = "PF"
                    current_community = pf_match.group(1).strip()
                    # Find matching progress entry and mark running
                    for tid, p in scraper_state["progress"].items():
                        if p["community"] == current_community and p["source"] == "PF" and p["status"] == "queued":
                            emit_progress(tid, {**p, "status": "running"})
                            break

                elif bayut_match:
                    current_source = "Bayut"
                    current_community = bayut_match.group(1).strip()
                    for tid, p in scraper_state["progress"].items():
                        if p["community"] == current_community and p["source"] == "Bayut" and p["status"] == "queued":
                            emit_progress(tid, {**p, "status": "running"})
                            break

                # Detect source section headers
                elif "── Property Finder" in clean:
                    current_source = "PF"
                elif "── Bayut" in clean:
                    current_source = "Bayut"

                # Detect success: "✓ PF DAMAC Lagoons: 5 listings" or "✓ Bayut DAMAC Lagoons: 8 listings"
                success_match = re.search(r'✓\s+(PF|Bayut)\s+(.+?):\s+(\d+)\s+listings', clean)
                if success_match:
                    src = success_match.group(1)
                    comm = success_match.group(2).strip()
                    count = int(success_match.group(3))
                    scraper_state["total_scraped"] += count
                    # Find matching running progress entry
                    for tid, p in scraper_state["progress"].items():
                        if p["community"] == comm and p["source"] == src and p["status"] in ("running", "retrying"):
                            emit_progress(tid, {**p, "status": "done", "listings": count})
                            break
                    else:
                        # Fallback: try to match any running entry with same source
                        for tid, p in scraper_state["progress"].items():
                            if p["source"] == src and p["status"] in ("running", "retrying"):
                                emit_progress(tid, {**p, "status": "done", "listings": count})
                                break
                    emit_stats()

                # Detect PF cookie error and auto-retry
                elif "No __NEXT_DATA__ found" in clean:
                    scraper_state["errors"] += 1
                    emit_alert("error", f"Cookie issue on {current_community}. Auto-retrying...")
                    for tid, p in scraper_state["progress"].items():
                        if p["status"] == "running":
                            emit_progress(tid, {**p, "status": "retrying"})
                            break
                    emit_stats()

                # Detect retry
                elif "↻ PF" in clean or "Retry" in clean:
                    for tid, p in scraper_state["progress"].items():
                        if p["status"] in ("running", "retrying") and (not current_community or p["community"] == current_community):
                            emit_progress(tid, {**p, "status": "retrying"})
                            break

                # Detect CAPTCHA pause: "🔒 CAPTCHA:WAITING:CommunityName"
                elif "CAPTCHA:WAITING:" in clean:
                    captcha_comm = clean.split("CAPTCHA:WAITING:")[-1].strip()
                    emit_alert("captcha_pause", f"CAPTCHA detected on {captcha_comm or current_community} — solve it in the browser, then click Continue", captcha_comm or current_community)
                    for tid, p in scraper_state["progress"].items():
                        if p["status"] in ("running", "retrying"):
                            emit_progress(tid, {**p, "status": "captcha"})
                            break

                # Detect CAPTCHA (generic)
                elif "CAPTCHA" in clean.upper() and ("detected" in clean.lower() or "🔒" in clean):
                    emit_alert("captcha", "CAPTCHA detected — please solve it in the browser window", current_community)
                    for tid, p in scraper_state["progress"].items():
                        if p["status"] in ("running", "retrying"):
                            emit_progress(tid, {**p, "status": "captcha"})
                            break

                # Detect CAPTCHA solved
                elif "CAPTCHA solved" in clean or "✅" in clean:
                    emit_alert("success", "CAPTCHA solved! Continuing...")
                    for tid, p in scraper_state["progress"].items():
                        if p["status"] == "captcha":
                            emit_progress(tid, {**p, "status": "running"})
                            break

                # Detect Continue signal received
                elif "Continue signal received" in clean:
                    for tid, p in scraper_state["progress"].items():
                        if p["status"] == "captcha":
                            emit_progress(tid, {**p, "status": "running"})
                            break

                # Detect errors
                elif "✗" in clean and current_community:
                    scraper_state["errors"] += 1
                    for tid, p in scraper_state["progress"].items():
                        if p["status"] in ("running", "retrying") and p["community"] == current_community:
                            emit_progress(tid, {**p, "status": "error"})
                            break
                    emit_stats()

                # Determine log level
                level = "info"
                if "✓" in clean or "✅" in clean:
                    level = "ok"
                elif "✗" in clean or "ERROR" in clean:
                    level = "err"
                elif "⚠" in clean or "CAPTCHA" in clean.upper() or "warn" in clean.lower():
                    level = "warn"

                emit_log(clean, level)

            proc.wait()

            # Mark any remaining "queued" entries as skipped if we stopped
            if scraper_state["stop_requested"]:
                for tid, p in scraper_state["progress"].items():
                    if p["status"] in ("queued", "running"):
                        emit_progress(tid, {**p, "status": "error"})

            # Run processor after scraping (only if not stopped)
            if not scraper_state["stop_requested"]:
                processor = "process_deals.py" if m == "villa" else "process_apartments.py"
                proc_path = os.path.join(SCRIPTS, processor)
                emit_log(f"Running processor: {processor}...", "info")
                proc2 = subprocess.Popen(
                    [sys.executable, "-u", proc_path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, cwd=BASE
                )
                for line2 in iter(proc2.stdout.readline, ""):
                    line2 = line2.strip()
                    if line2:
                        emit_log(line2, "ok" if "✓" in line2 else "info")
                        # Capture dedup count
                        if "Dedup:" in line2 or "dedup" in line2.lower():
                            m_d = re.search(r'→\s*(\d+)', line2)
                            if m_d:
                                scraper_state["total_deduped"] = int(m_d.group(1))
                                emit_stats()
                proc2.wait()
                emit_log(f"{m.capitalize()} pipeline complete!", "ok")

        except Exception as e:
            emit_log(f"Scraper error: {e}", "err")
            scraper_state["errors"] += 1

    # All done
    scraper_state["running"] = False
    scraper_state["proc"] = None
    emit_log("All scraping complete!", "ok")
    socketio.emit("scrape_done", {
        "total_scraped": scraper_state["total_scraped"],
        "total_deduped": scraper_state["total_deduped"],
        "errors": scraper_state["errors"],
    })


# ─── SOCKET EVENTS ────────────────────────────────────────────────────────────
@socketio.on("start_scrape")
def handle_start(data):
    if scraper_state["running"]:
        emit("log", {"time": datetime.now().strftime("%H:%M:%S"),
                      "msg": "Scraper already running!", "level": "warn"})
        return

    mode = data.get("mode", "villa")
    source = data.get("source", "all")
    selected = data.get("communities")  # optional list of names

    scraper_state["mode"] = mode
    scraper_state["source"] = source

    thread = threading.Thread(
        target=run_scraper_thread,
        args=(mode, source, selected),
        daemon=True
    )
    thread.start()
    emit("scrape_started", {"mode": mode, "source": source})


@socketio.on("stop_scrape")
def handle_stop(_data=None):
    scraper_state["stop_requested"] = True
    # Also try to kill the subprocess immediately
    proc = scraper_state.get("proc")
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            emit_log("Sending stop signal to scraper...", "warn")
        except (OSError, ProcessLookupError):
            try:
                proc.terminate()
            except Exception:
                pass
    else:
        emit_log("Stop requested — finishing current operation...", "warn")


@socketio.on("connect")
def handle_connect():
    emit_log("Client connected", "info")
    # Send current state if scraper is running
    if scraper_state["running"]:
        emit("scrape_started", {"mode": scraper_state["mode"], "source": scraper_state["source"]})
        # Resend all progress entries
        for tid, p in scraper_state["progress"].items():
            emit("progress", {"task_id": tid, **p})
        emit_stats()


# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper Control Panel")
    parser.add_argument("--port", type=int, default=8080, help="Port to run on")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    print(f"\n  Dubai Scraper Control Panel")
    print(f"  http://localhost:{args.port}\n")

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    socketio.run(app, host="0.0.0.0", port=args.port, debug=False, allow_unsafe_werkzeug=True)
