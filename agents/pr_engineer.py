"""
PR Engineer Agent
Responsible for performing context harvest, querying local LLMs, and generating code fixes.
Executes the 'Comment First' strategy to propose fixes to maintainers before full implementation.
Emits 'PR_READY' events upon generating a valid patch.
"""

import os
import shutil
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import git
import docker
import subprocess
import re
from events import PRReadyEvent
from utils.github_api import SafeGitHubSession
from utils.database import Database
from typing import Callable, Any

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PREngineer:
    """
    Agent responsible for analyzing the codebase, posting proposal comments, 
    and generating the actual patch using local AI models with fallback support.
    """
    
    def __init__(self, publish_event: Callable[[Any], None], stealth_mode: bool = False):
        """
        Initialize the PREngineer.
        
        Args:
            publish_event: Callback function to emit events to the orchestrator.
            stealth_mode: If true, act like a human to avoid bot flags.
        """
        self.publish_event = publish_event
        self.stealth_mode = stealth_mode
        self.workspace_root = os.path.join(os.getcwd(), "workspaces")
        os.makedirs(self.workspace_root, exist_ok=True)
        self.github_token = os.environ.get("GITHUB_TOKEN", None)
        self.timeout = 600
        self.db = Database()
        
        # Ensure docker is available
        self._docker_client = None

    @property
    def docker_client(self):
        """Dynamically fetch the docker client to survive daemon restarts."""
        try:
            if not self._docker_client:
                self._docker_client = docker.from_env()
            else:
                self._docker_client.ping()
            return self._docker_client
        except Exception as e:
            logger.warning(f"PREngineer: Docker client failed to initialize or reconnect: {e}. Sandboxing disabled.")
            self._docker_client = None
            return None

    def get_session(self):
        """Returns a configured requests session for AI or API queries."""
        return SafeGitHubSession()

    def post_comment(self, repo_name: str, issue_number: str):
        """
        Implements the 'Comment-First' strategy to build trust with maintainers.
        """
        if not self.github_token or not issue_number:
            return
        
        logger.info(f"PREngineer: Comment-First Strategy -> Posting to #{issue_number}")
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {self.github_token}"
        }
        
        stealth_msg = "Hey! I was just looking through the codebase and noticed this issue. Taking a stab at fixing it now, I'll send over a PR if I get it working!"
        bot_msg = "Hi! I've analyzed the issue and identified the root cause. I'm preparing a minimal fix with tests matching the repo's style. I will submit a PR shortly."
        
        if self.stealth_mode:
            comment = stealth_msg
        else:
            comment = bot_msg
            
        url = f"https://api.github.com/repos/{repo_name}/issues/{issue_number}/comments"
        
        try:
            session = self.get_session()
            
            # Prevent duplicate comments regardless of current stealth mode
            res = session.get(url, headers=headers, timeout=self.timeout)
            if res.status_code == 200:
                existing_comments = res.json()
                if any(c.get("body", "") in [stealth_msg, bot_msg] for c in existing_comments):
                    logger.info(f"PREngineer: Comment already exists on #{issue_number}. Skipping duplicate.")
                    return
                    
            session.post(url, headers=headers, json={"body": comment}, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            logger.error(f"PREngineer: Failed to post comment: {e}")

    def gather_context(self, repo_path: str, issue_title: str = "", issue_body: str = "", comments: list = []) -> str:
        """
        Performs a 'Context Harvest' to learn the repository's coding style and rules.
        Also extracts any mentioned files from the issue body or comments and injects their code!
        """
        logger.info("PREngineer: Context Harvest -> Gathering repo style and structure.")
        context = ""
        try:
            # 0. Directory Structure (Skeleton)
            dir_context = "Directory Structure:\n"
            for root, dirs, files in os.walk(repo_path):
                # Ignore noisy directories
                dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', 'target', 'venv', '__pycache__', 'dist', 'build']]
                level = root.replace(repo_path, '').count(os.sep)
                indent = ' ' * 4 * level
                dir_context += f"{indent}{os.path.basename(root)}/\n"
                subindent = ' ' * 4 * (level + 1)
                for f in files:
                    dir_context += f"{subindent}{f}\n"
                    
            if len(dir_context) > 3000:
                dir_context = dir_context[:3000] + "\n...[DIRECTORY TRUNCATED]..."
            context += dir_context + "\n\n"

            # 1. Pull README.md
            readme_path = os.path.join(repo_path, "README.md")
            if os.path.exists(readme_path):
                with open(readme_path, "r", encoding="utf-8", errors="ignore") as f:
                    context += f"README.md:\n{f.read()[:1000]}\n\n"

            # 2. Pull contributing guidelines
            contrib_path = os.path.join(repo_path, "CONTRIBUTING.md")
            if os.path.exists(contrib_path):
                with open(contrib_path, "r", encoding="utf-8", errors="ignore") as f:
                    context += f"CONTRIBUTING.md:\n{f.read()[:500]}\n\n"
            
            # 3. Get recent commits for commit message style
            repo = git.Repo(repo_path)
            commits = list(repo.iter_commits(max_count=5))
            context += "Recent Commits:\n"
            for c in commits:
                context += f"- {c.message.strip()}\n"
            # 4. Context RAG: Extract mentioned files AND use TF-IDF to find relevant files
            text_to_search = issue_title + "\n" + issue_body + "\n" + "\n".join(comments)
            potential_files = set(re.findall(r'[\w/.-]+\.\w+', text_to_search))
            
            # TF-IDF Zero-Setup RAG
            try:
                from sklearn.feature_extraction.text import TfidfVectorizer
                from sklearn.metrics.pairwise import cosine_similarity
                
                all_files = []
                file_contents = []
                for root, dirs, files in os.walk(repo_path):
                    dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', 'target', 'venv', '__pycache__', 'dist', 'build', '.idea', '.vscode']]
                    for f in files:
                        full_path = os.path.join(root, f)
                        try:
                            with open(full_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                                content = file_obj.read()
                                if content.strip():
                                    all_files.append(full_path)
                                    # Prefix filename so filename matches give high score
                                    file_contents.append(f"{f} {f} {content}")
                        except:
                            pass
                
                if file_contents:
                    vectorizer = TfidfVectorizer(stop_words='english', max_features=10000)
                    tfidf_matrix = vectorizer.fit_transform(file_contents + [text_to_search])
                    cosine_sim = cosine_similarity(tfidf_matrix[-1], tfidf_matrix[:-1]).flatten()
                    top_indices = cosine_sim.argsort()[-3:][::-1]  # Top 3 most similar files
                    for idx in top_indices:
                        if cosine_sim[idx] > 0.05:  # Arbitrary relevance threshold
                            rel_path = os.path.relpath(all_files[idx], repo_path).replace("\\", "/")
                            potential_files.add(rel_path)
                            logger.info(f"PREngineer: RAG fetched {rel_path} (score: {cosine_sim[idx]:.3f})")
            except Exception as e:
                logger.warning(f"PREngineer: TF-IDF RAG failed (is scikit-learn installed?): {e}")

            context += "Relevant Source Code:\n"
            found_any = False
            
            ast_dependencies = set()
            try:
                from utils.ast_parser import extract_local_imports
            except ImportError:
                extract_local_imports = None

            for f in potential_files:
                # Basic protection against traversing up
                if ".." in f: continue
                full_path = os.path.join(repo_path, f.lstrip('/'))
                if os.path.isfile(full_path) and os.path.exists(full_path):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                            file_content = file_obj.read()
                            if len(file_content) > 4000:
                                file_content = file_content[:4000] + "\n...[FILE CONTENT TRUNCATED]..."
                            context += f"--- {f} ---\n{file_content}\n\n"
                            found_any = True
                            
                        # AST Context Upgrade
                        if full_path.endswith(".py") and extract_local_imports:
                            deps = extract_local_imports(full_path, repo_path)
                            for d in deps:
                                ast_dependencies.add(d)
                                
                    except Exception as e:
                        logger.warning(f"Failed to read extracted file {f}: {e}")
                        
            if ast_dependencies:
                context += "--- AST Dependency Context ---\n"
                for dep in ast_dependencies:
                    try:
                        with open(dep, "r", encoding="utf-8", errors="ignore") as file_obj:
                            rel_dep = os.path.relpath(dep, repo_path).replace("\\", "/")
                            file_content = file_obj.read()
                            if len(file_content) > 2500:
                                file_content = file_content[:2500] + "\n...[DEPENDENCY TRUNCATED]..."
                            context += f"--- {rel_dep} (Imported Dependency) ---\n{file_content}\n\n"
                    except: pass
            if not found_any:
                context += "No specific files mentioned or found.\n\n"

        except Exception as e:
            logger.error(f"PREngineer: Context Harvest failed: {e}")
            
        # Hard limit the context to 18,000 characters to prevent local model hallucination
        if len(context) > 18000:
            logger.warning("PREngineer: Context too large, truncating to 18k characters as final safety net.")
            context = context[:18000] + "\n...[CONTEXT TRUNCATED]..."
            
        return context

    def query_ai(self, prompt: str) -> str:
        """
        Queries the local AI models, iterating through fallbacks if necessary.
        """
        session = self.get_session()
        models = ["gemma4:e4b", "llama3", "mistral"]
        for model in models:
            logger.info(f"PREngineer: Querying local AI ({model})...")
            try:
                response = session.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": model, 
                        "prompt": prompt, 
                        "stream": False,
                        "options": {"num_ctx": 8192}
                    },
                    timeout=self.timeout
                )
                if response.status_code == 200:
                    return response.json().get("response", "")
            except requests.exceptions.RequestException as e:
                logger.warning(f"PREngineer: AI request with {model} failed: {e}. Falling back...")
        
        raise Exception("All fallback models failed to generate a response.")

    def verify_syntax(self, code: str, filepath: str) -> tuple[bool, str]:
        """
        Pre-Flight Syntax Check to instantly block hallucinated slop.
        """
        if not filepath.endswith(".py"):
            return True, ""
            
        import ast
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            msg = f"SyntaxError at line {e.lineno}, offset {e.offset}: {e.msg}\nCode snippet:\n{e.text}"
            logger.warning(f"PREngineer: Pre-Flight Syntax Check failed for {filepath} -> {msg}")
            return False, msg

    def parse_and_apply_files(self, response: str, repo_path: str) -> list[str]:
        import re
        pattern = r'<file path="([^"]+)">\s*(.*?)\s*</file>'
        matches = list(re.finditer(pattern, response, re.DOTALL | re.IGNORECASE))
        if not matches:
            logger.warning("PREngineer: No [FILE: ...] blocks found in AI response.")
            return []
            
        modified_files = []
        for match in matches:
            filepath = match.group(1).strip()
            code = match.group(2)
            
            # Strip markdown wrappers if AI hallucinates them inside the tags
            if code.startswith("```"):
                code = re.sub(r'^```[a-zA-Z]*\n', '', code)
            if code.endswith("```"):
                code = code[:-3].rstrip()
            if code.endswith("```\n"):
                code = code[:-4].rstrip()
                
            if ".." in filepath: continue
            
            # Pre-flight check
            is_valid, err_msg = self.verify_syntax(code, filepath)
            if not is_valid:
                raise Exception(f"AI generated invalid syntax for {filepath}: {err_msg}")
                
            full_path = os.path.join(repo_path, filepath.lstrip('/'))
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(code)
            logger.info(f"PREngineer: Patched {filepath}")
            modified_files.append(filepath)
            
        return modified_files

    def run_tests(self, repo_path: str) -> tuple[bool, str]:
        if not self.docker_client:
            return True, "Docker disabled, skipping tests."
        
        cmds = ["apt-get update -qq", "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential curl git"]
        if os.path.exists(os.path.join(repo_path, "package.json")):
            cmds.extend(["curl -fsSL https://deb.nodesource.com/setup_20.x | bash -", "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs", "npm install", "npx eslint . --no-error-on-unmatched-pattern 2>/dev/null || true", "npm test"])
        elif os.path.exists(os.path.join(repo_path, "requirements.txt")) or os.path.exists(os.path.join(repo_path, "setup.py")) or os.path.exists(os.path.join(repo_path, "pyproject.toml")):
            cmds.extend(["DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-pip python3-venv", "python3 -m venv venv", ". venv/bin/activate", "pip install pytest flake8", "pip install -e . 2>/dev/null || pip install -r requirements.txt 2>/dev/null", "flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics || true", "pytest"])
        elif os.path.exists(os.path.join(repo_path, "Cargo.toml")):
            cmds.extend(["curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y", "export PATH=\"$HOME/.cargo/bin:$PATH\"", "cargo check", "cargo test"])
        elif os.path.exists(os.path.join(repo_path, "go.mod")):
            cmds.extend(["DEBIAN_FRONTEND=noninteractive apt-get install -y -qq golang", "go build ./...", "go test ./..."])
        elif os.path.exists(os.path.join(repo_path, "pom.xml")):
            cmds.extend(["DEBIAN_FRONTEND=noninteractive apt-get install -y -qq maven openjdk-17-jdk", "mvn test"])
        elif os.path.exists(os.path.join(repo_path, "Gemfile")):
            cmds.extend(["DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ruby ruby-dev bundler", "bundle install", "bundle exec rspec"])
        elif os.path.exists(os.path.join(repo_path, "composer.json")):
            cmds.extend(["DEBIAN_FRONTEND=noninteractive apt-get install -y -qq php composer", "composer install", "vendor/bin/phpunit"])
        else:
            return True, "No standard tests found."

        script = " && ".join(cmds)
        try:
            logger.info("PREngineer: Running sandbox tests...")
            container = self.docker_client.containers.run(
                "ubuntu:22.04",
                command=["sh", "-c", script],
                volumes={repo_path: {'bind': '/workspace', 'mode': 'rw'}},
                working_dir="/workspace",
                detach=True
            )
            try:
                result = container.wait(timeout=300)
                logs = container.logs().decode("utf-8", errors="ignore")
                container.remove(force=True)
                
                if result.get("StatusCode") == 0:
                    logger.info("PREngineer: Tests passed!")
                    return True, logs
                else:
                    if "Missing script: \"test\"" in logs or "Missing script: test" in logs:
                        logger.info("PREngineer: Project has no tests configured. Treating successful build as a pass.")
                        return True, logs
                        
                    # Laser-focused error extraction
                    import re
                    tb_match = re.search(r'(Traceback \(most recent call last\):.*?)(?=\n\w+:|\Z)', logs, re.DOTALL)
                    if tb_match:
                        extracted = "CRITICAL TEST TRACEBACK:\n" + tb_match.group(1)[:2000]
                    else:
                        extracted = "TEST FAILURES:\n" + logs[-3000:] # Just grab the last 3k chars if no traceback
                        
                    logger.warning(f"PREngineer: Tests failed. Extracted traceback for AI.")
                    return False, extracted
            except Exception as test_err:
                logger.error(f"PREngineer: Tests timed out or threw an exception! Killing container. Error: {test_err}")
                try:
                    container.stop(timeout=2)
                    container.remove(force=True)
                except: pass
                return False, f"Tests timed out or failed to execute properly. Error: {test_err}"
        except Exception as e:
            logger.error(f"PREngineer: Docker error: {e}")
            return False, str(e)

    def solve_issue(self, payload: dict):
        """
        The main pipeline to generate a fix for an issue.
        """
        repo_name = payload.get('repo')
        issue_title = payload.get('issue_title')
        issue_body = payload.get('issue_body', '')
        issue_number = payload.get('issue_number')
        
        issue_url = payload.get('issue_url', '')
        
        if not repo_name or not issue_title:
            logger.error("PREngineer: Invalid payload received. Missing repo or title.")
            return
            
        retry_count = payload.get('retry_count', 0)
        is_retry = retry_count > 0
        reviewer_feedback = payload.get('reviewer_feedback', '')
        
        if not is_retry:
            # Mark issue as pending in DB
            self.db.mark_issue(issue_url, repo_name, "PENDING")
            logger.info(f"PREngineer: Starting work on {repo_name} - {issue_title}")

            # 2. Clone the repository safely (Scoped by issue_number to prevent race condition nukes)
            safe_issue_num = str(issue_number).replace("/", "_")
            repo_path = os.path.join(self.workspace_root, f"{repo_name.replace('/', '_')}_{safe_issue_num}")
            if os.path.exists(repo_path):
                logger.info(f"PREngineer: Cleaning up old workspace for {repo_name}...")
                if os.name == 'nt':
                    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", repo_path], check=False)
                else:
                    shutil.rmtree(repo_path, ignore_errors=True)
            
            try:
                logger.info(f"PREngineer: Cloning https://github.com/{repo_name}.git ...")
                git.Repo.clone_from(f"https://github.com/{repo_name}.git", repo_path, depth=1)
            except Exception as e:
                logger.error(f"PREngineer: Failed to clone {repo_name}: {e}")
                return

            # Fetch issue comments
            comments = []
            if self.github_token and issue_number:
                try:
                    url = f"https://api.github.com/repos/{repo_name}/issues/{issue_number}/comments"
                    res = self.get_session().get(url, headers={"Authorization": f"token {self.github_token}"}, timeout=30)
                    if res.status_code == 200:
                        comments = [c.get("body", "") for c in res.json()]
                except Exception as e:
                    logger.error(f"PREngineer: Failed to fetch comments: {e}")

            # 3. Context Harvest
            repo_context = self.gather_context(repo_path, issue_title, issue_body, comments)
        else:
            logger.info(f"PREngineer: Retrying fix based on test/review feedback (Attempt {retry_count + 1})")
            repo_path = payload.get('workspace_path')
            if not repo_path or not os.path.exists(repo_path):
                logger.error("PREngineer: Workspace missing during retry. Aborting.")
                return
            repo_context = self.gather_context(repo_path, issue_title, issue_body)

        # 4. Iterative Generate and Test Loop
        max_internal_retries = 2
        test_feedback = ""
        
        for attempt in range(max_internal_retries + 1):
            if attempt > 0:
                logger.info(f"PREngineer: Internal test retry {attempt}/{max_internal_retries}")
                
            if not is_retry and attempt == 0:
                prompt = f"""You are a senior open-source contributor. Your sole goal is to fix the described issue WITHOUT rewriting unrelated code.

RULES:
1. MATCH THE EXISTING CODE STYLE EXACTLY. Do not rename variables or change formatting unless necessary.
2. DO NOT output 'AI Slop' (e.g., removing necessary comments, over-explaining in code comments, or adding massive refactors).
3. Keep changes absolutely minimal — fix only what the issue describes.
4. OUTPUT FORMAT REQUIRED: To modify a file, you MUST use XML tags. 
DO NOT wrap the XML tags in markdown. DO NOT wrap the code inside the XML tags in markdown.

GOOD EXAMPLE:
<file path="src/utils.py">
def helper():
    return True
</file>

BAD EXAMPLE (DO NOT DO THIS):
```python
<file path="src/utils.py">
def helper():
    return True
</file>
```

CONTEXT:
{repo_context}

ISSUE:
{issue_title}

Respond with only the code changes needed and a summary for the PR description."""
            else:
                previous_fix = payload.get('proposed_fix', '')
                feedback_str = reviewer_feedback if (is_retry and attempt == 0) else test_feedback
                source = "Code Reviewer" if (is_retry and attempt == 0) else "Unit Tests"
                
                prompt = f"""You are a senior open-source contributor. You previously wrote this fix for the issue '{issue_title}':

{previous_fix}

However, the {source} REJECTED it with the following feedback/errors:

{feedback_str}

CONTEXT:
{repo_context}

Please provide a completely revised fix. 
Please provide a completely revised fix that addresses the feedback. 

OUTPUT FORMAT REQUIRED: To modify a file, you MUST use XML tags. 
DO NOT wrap the XML tags in markdown. DO NOT wrap the code inside the XML tags in markdown.

GOOD EXAMPLE:
<file path="src/utils.py">
def helper():
    return True
</file>"""

            try:
                ai_response = self.query_ai(prompt)
                
                from utils.logger import log_ollama_activity
                log_ollama_activity("PREngineer", prompt, ai_response)
                
                logger.info(f"PREngineer: AI successfully generated a proposed fix of {len(ai_response)} characters.")
                
                # Apply files
                try:
                    modified_files = self.parse_and_apply_files(ai_response, repo_path)
                except Exception as syntax_err:
                    logger.warning("PREngineer: Syntax error detected in AI response. Triggering immediate retry.")
                    test_feedback = str(syntax_err)
                    payload["proposed_fix"] = ai_response
                    continue
                
                if not modified_files:
                    logger.warning("PREngineer: AI response did not contain valid file modifications.")
                    test_feedback = "You failed to use the <file path=\"...\">...</file> format. No files were modified. You MUST format your code using the XML tags exactly as requested."
                    payload["proposed_fix"] = ai_response
                    continue
                    
                payload["modified_files"] = modified_files
                
                # Run Tests
                success, output = self.run_tests(repo_path)
                if success:
                    # Only post the comment-first strategy if we ACTUALLY got a fix.
                    if not is_retry:
                        self.post_comment(repo_name, issue_number)
                        
                    payload["proposed_fix"] = ai_response
                    payload["workspace_path"] = repo_path
                    self.publish_event(PRReadyEvent(payload=payload))
                    return
                else:
                    test_feedback = output
                    payload["proposed_fix"] = ai_response # Save for next prompt
            except Exception as e:
                logger.error(f"PREngineer: Solution generation failed: {e}")
                return
                
        if "modified_files" not in payload:
            logger.error("PREngineer: Failed to generate any valid patches after max retries. Aborting PR.")
            return
            
        logger.error("PREngineer: Failed to pass tests after max retries. Emitting PR anyway for manual review.")
        payload["workspace_path"] = repo_path
        self.publish_event(PRReadyEvent(payload=payload))
