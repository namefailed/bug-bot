from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import sqlite3
import yaml
import json
import os
import sys
import subprocess
import psutil
import asyncio
import requests
import datetime
from utils.database import Database
from utils.env import load_env_file
from agents.code_reviewer import CodeReviewer

app = FastAPI()

# This dashboard exposes privileged endpoints (start the bot, edit config, submit PRs)
# and reads the GitHub token. It is a LOCAL tool: bind it to 127.0.0.1 (see __main__ /
# the README) and restrict CORS to the local origin so other sites a browser visits
# cannot read responses or forge cross-origin requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Keys that must never be sent to the browser.
SECRET_CONFIG_KEYS = ("github_token",)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bounty_tracker.db")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
ACTIVITY_LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ollama_activity.json")
ORCHESTRATOR_LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "codemechanic.log")
UI_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
BOT_PROCESS = None
LOG_FILE_HANDLE = None

def load_env_from_config():
    """
    Ensures GITHUB_TOKEN is available to the dashboard process. Prefers .env (the
    same file the orchestrator reads), then falls back to a token in config.yaml.
    """
    load_env_file(ENV_PATH)
    if not os.environ.get("GITHUB_TOKEN") and os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f) or {}
                if "github_token" in cfg:
                    os.environ["GITHUB_TOKEN"] = cfg["github_token"]
        except Exception:
            pass

@app.on_event("startup")
async def start_pr_poller():
    load_env_from_config()
    asyncio.create_task(poll_prs_loop())

async def poll_prs_loop():
    while True:
        try:
            if os.path.exists(DB_PATH):
                load_env_from_config()
                github_token = os.environ.get("GITHUB_TOKEN")
                if github_token:
                    conn = sqlite3.connect(DB_PATH)
                    cur = conn.cursor()
                    cur.execute("SELECT issue_url, pr_api_url, bounty_value FROM processed_issues WHERE status = 'SUBMITTED' AND pr_api_url IS NOT NULL")
                    rows = cur.fetchall()
                    
                    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
                    
                    for issue_url, pr_api_url, bounty_value in rows:
                        try:
                            # Use run_in_executor to avoid blocking the event loop entirely, or just block since it's a small internal app
                            res = requests.get(pr_api_url, headers=headers, timeout=10)
                            if res.status_code == 200:
                                data = res.json()
                                if data.get("merged") == True:
                                    cur.execute("UPDATE processed_issues SET status = 'PAYOUT_CONFIRMED', amount_earned = ? WHERE issue_url = ?", (bounty_value, issue_url))
                                    conn.commit()
                                elif data.get("state") == "closed" and not data.get("merged"):
                                    cur.execute("UPDATE processed_issues SET status = 'REJECTED' WHERE issue_url = ?", (issue_url,))
                                    conn.commit()
                        except: pass
                    conn.close()
        except Exception as e:
            pass
        
        await asyncio.sleep(60)

@app.get("/api/status")
def get_status():
    global BOT_PROCESS
    is_running = False
    if BOT_PROCESS:
        try:
            # Check if process is actually running
            p = psutil.Process(BOT_PROCESS.pid)
            if p.status() != psutil.STATUS_ZOMBIE:
                is_running = True
        except psutil.NoSuchProcess:
            BOT_PROCESS = None
    return {"status": "running" if is_running else "stopped"}

@app.post("/api/bot/start")
def start_bot(stealth: bool = False):
    global BOT_PROCESS, LOG_FILE_HANDLE
    if BOT_PROCESS and psutil.pid_exists(BOT_PROCESS.pid):
        return {"message": "Already running"}
    
    # Use the same interpreter that's running the API (the venv), not whatever
    # "python" resolves to on PATH.
    cmd = [sys.executable, "orchestrator.py"]
    if stealth:
        cmd.append("--stealth")
        
    cwd = os.path.dirname(os.path.dirname(__file__))
    
    # Open the log file to pipe stdout and stderr
    LOG_FILE_HANDLE = open(ORCHESTRATOR_LOG, "a", encoding="utf-8")
    BOT_PROCESS = subprocess.Popen(cmd, cwd=cwd, stdout=LOG_FILE_HANDLE, stderr=subprocess.STDOUT)
    return {"message": "Bot started"}

@app.post("/api/bot/stop")
def stop_bot():
    global BOT_PROCESS, LOG_FILE_HANDLE
    if BOT_PROCESS and psutil.pid_exists(BOT_PROCESS.pid):
        try:
            parent = psutil.Process(BOT_PROCESS.pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
        except psutil.NoSuchProcess:
            pass
        BOT_PROCESS = None
        
    if LOG_FILE_HANDLE:
        try:
            LOG_FILE_HANDLE.close()
        except Exception:
            pass
        LOG_FILE_HANDLE = None
        
    return {"message": "Bot stopped"}

@app.get("/api/config")
def get_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f) or {}
    # Never disclose secrets to the browser.
    for key in SECRET_CONFIG_KEYS:
        cfg.pop(key, None)
    return cfg

@app.post("/api/config")
def save_config(config_data: dict):
    # Preserve secret keys that were redacted from GET, so saving the dashboard's
    # view does not silently wipe the stored token.
    existing = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            existing = yaml.safe_load(f) or {}
    for key in SECRET_CONFIG_KEYS:
        if key not in config_data and key in existing:
            config_data[key] = existing[key]
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(config_data, f)
    return {"message": "Config saved"}

@app.get("/api/prs")
def get_prs():
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT issue_url, repo_name, status, updated_at FROM processed_issues ORDER BY updated_at DESC")
        rows = cur.fetchall()
        return [{"issue_url": r[0], "repo": r[1], "status": r[2], "updated_at": r[3]} for r in rows]
    except Exception as e:
        return []

@app.get("/api/activity")
def get_activity():
    if not os.path.exists(ACTIVITY_LOG):
        return []
    try:
        with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

@app.get("/api/analytics")
def get_analytics():
    if not os.path.exists(DB_PATH):
        return {"status_counts": {}, "daily_activity": {}}
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        # Status counts
        cur.execute("SELECT status, COUNT(*) FROM processed_issues GROUP BY status")
        status_counts = {row[0]: row[1] for row in cur.fetchall()}
        
        # Daily activity (last 7 days sum of amount_earned)
        cur.execute("SELECT date(updated_at), SUM(amount_earned) FROM processed_issues WHERE updated_at >= date('now', '-7 days') GROUP BY date(updated_at) ORDER BY date(updated_at)")
        daily_activity = {row[0]: (row[1] or 0) for row in cur.fetchall()}
        
        return {"status_counts": status_counts, "daily_activity": daily_activity}
    except Exception as e:
        return {"status_counts": {}, "daily_activity": {}, "error": str(e)}

@app.get("/api/logs")
def get_logs():
    if not os.path.exists(ORCHESTRATOR_LOG):
        return {"logs": "No logs yet."}
    try:
        # Efficient tail: read from end of file
        with open(ORCHESTRATOR_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            # Read last 16KB which should cover a couple hundred lines
            bytes_to_read = min(16384, file_size)
            f.seek(file_size - bytes_to_read, os.SEEK_SET)
            content = f.read().decode("utf-8", errors="replace")
            
        # Return lines (skipping the first potentially partial line)
        lines = content.split('\n')
        if len(lines) > 200:
            return {"logs": '\n'.join(lines[-200:])}
        else:
            if bytes_to_read == file_size:
                return {"logs": '\n'.join(lines)}
            return {"logs": '\n'.join(lines[1:])}
    except Exception as e:
        return {"logs": f"Error reading logs: {e}"}

class ApprovalRequest(BaseModel):
    issue_url: str
    edited_code: str = None

@app.get("/api/approvals")
def get_approvals():
    db = Database(DB_PATH)
    return db.get_pending_approvals()

@app.post("/api/approvals/approve")
def approve_pr(req: ApprovalRequest):
    db = Database(DB_PATH)
    pending = db.get_pending_approvals()
    target = next((p for p in pending if p["issue_url"] == req.issue_url), None)
    if not target:
        raise HTTPException(status_code=404, detail="Pending approval not found")
        
    code_to_submit = req.edited_code if req.edited_code else target["proposed_fix"]
    modified_files = json.loads(target["modified_files"])
    
    # Ensure token is loaded into environment before init
    load_env_from_config()
    
    # Initialize standalone CodeReviewer just for submission
    # We pass a dummy lambda for the event bus publish
    reviewer = CodeReviewer(lambda x: None)
    
    success = reviewer.submit_pr(
        repo_name=target["repo_name"],
        issue_title=target["issue_title"],
        issue_number=target["issue_number"],
        proposed_fix=code_to_submit,
        workspace_path=target["workspace_path"],
        modified_files=modified_files
    )
    
    if success:
        db.remove_pending_approval(req.issue_url)
        db.mark_issue(req.issue_url, target["repo_name"], "SUBMITTED")
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to submit PR via GitHub API.")

@app.post("/api/approvals/reject")
def reject_pr(req: ApprovalRequest):
    db = Database(DB_PATH)
    target = next((p for p in db.get_pending_approvals() if p["issue_url"] == req.issue_url), None)
    if not target:
        raise HTTPException(status_code=404, detail="Pending approval not found")
        
    db.remove_pending_approval(req.issue_url)
    db.mark_issue(req.issue_url, target["repo_name"], "REJECTED_MANUALLY")
    
    import shutil
    import requests
    
    workspace_path = target.get("workspace_path")
    comment_id = target.get("comment_id")
    repo_name = target.get("repo_name")
    
    if workspace_path and os.path.exists(workspace_path):
        shutil.rmtree(workspace_path, ignore_errors=True)
        
    load_env_from_config()
    github_token = os.environ.get("GITHUB_TOKEN")
    if comment_id and github_token:
        try:
            headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
            requests.delete(f"https://api.github.com/repos/{repo_name}/issues/comments/{comment_id}", headers=headers, timeout=10)
        except:
            pass
            
    return {"status": "rejected"}

if os.path.exists(UI_PATH):
    app.mount("/", StaticFiles(directory=UI_PATH, html=True), name="ui")

if __name__ == "__main__":
    import uvicorn
    # Bind to loopback only — this dashboard can start the bot and holds the GitHub
    # token. Do not expose it on 0.0.0.0 without adding authentication first.
    uvicorn.run(app, host="127.0.0.1", port=8000)
