import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path="bounty_tracker.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        
    def _init_db(self):
        try:
            with self.conn:
                self.conn.executescript('''
                    CREATE TABLE IF NOT EXISTS processed_issues (
                        issue_url TEXT PRIMARY KEY,
                        repo_name TEXT,
                        status TEXT,
                        bounty_value NUMERIC DEFAULT 0,
                        pr_api_url TEXT,
                        amount_earned NUMERIC DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS processed_comments (
                        comment_id TEXT PRIMARY KEY
                    );
                    CREATE TABLE IF NOT EXISTS pending_approvals (
                        issue_url TEXT PRIMARY KEY,
                        repo_name TEXT,
                        issue_title TEXT,
                        issue_number TEXT,
                        proposed_fix TEXT,
                        ai_summary TEXT,
                        workspace_path TEXT,
                        modified_files TEXT,
                        comment_id TEXT
                    );
                ''')
        except Exception as e:
            logger.error(f"Database init failed: {e}")
            
    def mark_issue(self, issue_url: str, repo_name: str, status: str, bounty_value: float = 0.0, pr_api_url: str = None, amount_earned: float = 0.0):
        try:
            with self.conn:
                self.conn.execute('''
                    INSERT INTO processed_issues (issue_url, repo_name, status, bounty_value, pr_api_url, amount_earned, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(issue_url) DO UPDATE SET
                        status=excluded.status,
                        repo_name=excluded.repo_name,
                        bounty_value=COALESCE(NULLIF(excluded.bounty_value, 0.0), processed_issues.bounty_value),
                        pr_api_url=COALESCE(excluded.pr_api_url, processed_issues.pr_api_url),
                        amount_earned=COALESCE(NULLIF(excluded.amount_earned, 0.0), processed_issues.amount_earned),
                        updated_at=CURRENT_TIMESTAMP
                ''', (issue_url, repo_name, status, bounty_value, pr_api_url, amount_earned))
        except Exception as e:
            logger.error(f"Failed to mark issue {issue_url}: {e}")
            
    def get_status(self, issue_url: str) -> str:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT status FROM processed_issues WHERE issue_url = ?", (issue_url,))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"Failed to get status for {issue_url}: {e}")
            return None

    def get_pending_issues(self) -> list:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT issue_url, repo_name FROM processed_issues WHERE status = 'PENDING'")
            return [{"issue_url": row[0], "repo": row[1]} for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Failed to fetch pending issues: {e}")
            return []

    def is_comment_processed(self, comment_id: str) -> bool:
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM processed_comments WHERE comment_id = ?", (comment_id,))
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Failed to check comment {comment_id}: {e}")
            return False

    def mark_comment_processed(self, comment_id: str):
        try:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO processed_comments (comment_id) VALUES (?)", (comment_id,))
        except Exception as e:
            logger.error(f"Failed to mark comment {comment_id}: {e}")

    def save_pending_approval(self, issue_url: str, repo_name: str, issue_title: str, issue_number: str, proposed_fix: str, ai_summary: str, workspace_path: str, modified_files: str, comment_id: str = ""):
        try:
            with self.conn:
                self.conn.execute('''
                    INSERT OR REPLACE INTO pending_approvals 
                    (issue_url, repo_name, issue_title, issue_number, proposed_fix, ai_summary, workspace_path, modified_files, comment_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (issue_url, repo_name, issue_title, issue_number, proposed_fix, ai_summary, workspace_path, modified_files, comment_id))
        except Exception as e:
            logger.error(f"Failed to save pending approval {issue_url}: {e}")

    def get_pending_approvals(self) -> list:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT issue_url, repo_name, issue_title, issue_number, proposed_fix, ai_summary, workspace_path, modified_files, comment_id FROM pending_approvals")
            cols = ["issue_url", "repo_name", "issue_title", "issue_number", "proposed_fix", "ai_summary", "workspace_path", "modified_files", "comment_id"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Failed to fetch pending approvals: {e}")
            return []
            
    def remove_pending_approval(self, issue_url: str):
        try:
            with self.conn:
                self.conn.execute("DELETE FROM pending_approvals WHERE issue_url = ?", (issue_url,))
        except Exception as e:
            logger.error(f"Failed to remove pending approval {issue_url}: {e}")
