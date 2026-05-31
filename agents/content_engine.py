"""
Content Engine Agent
Responsible for autonomously drafting and publishing blog posts about
successfully submitted PRs, effectively building an audience while the bot sleeps.
"""

import os
import logging
from typing import Callable, Any
import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ContentEngine:
    """
    Agent that generates markdown blog posts summarizing the fixes applied by the bot.
    """
    
    def __init__(self, publish_event: Callable[[Any], None]):
        """
        Initialize the ContentEngine.
        
        Args:
            publish_event: Callback function to emit events to the orchestrator.
        """
        self.publish_event = publish_event
        self.posts_dir = os.path.join(os.getcwd(), "blog_posts")
        os.makedirs(self.posts_dir, exist_ok=True)

    def draft_post(self, payload: dict):
        """
        Drafts a blog post for a submitted PR.
        
        Args:
            payload: Dictionary containing 'repo' and 'issue_title'.
        """
        repo_name = payload.get('repo', 'unknown-repo')
        issue_title = payload.get('issue_title', 'Resolved Issue')
        
        logger.info(f"ContentEngine: Drafting blog post for {repo_name}...")
        
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        safe_repo_name = repo_name.replace('/', '_')
        filename = f"{date_str}-{safe_repo_name}.md"
        filepath = os.path.join(self.posts_dir, filename)
        
        content = (
            f"# Autonomously Fixing {repo_name}\n\n"
            f"**Date:** {date_str}\n"
            f"**Issue:** {issue_title}\n\n"
            f"## The Problem\n"
            f"We detected an open bounty on GitHub for this issue. Our ScamDetector validated the repository's authenticity.\n\n"
            f"## The Solution\n"
            f"Our autonomous PR Engineer cloned the code into a secure sandbox, utilized our local AI model to generate a fix, and successfully submitted a Pull Request!\n\n"
            f"*This post was generated autonomously by the CodeMechanic-Bot Content Engine.*"
        )
        
        try:
            with open(filepath, "w") as f:
                f.write(content)
            logger.info(f"ContentEngine: Successfully saved blog post to {filepath}")
        except Exception as e:
            logger.error(f"ContentEngine: Failed to save blog post: {e}")
