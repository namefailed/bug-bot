"""
Orchestrator
The central brain of CodeMechanic-Bot.
Initializes the EventBus and all Agents. Maps events to their respective agent handlers.
Runs the continuous scanning loop.
"""

import os
import time
import yaml
import logging
import argparse
from typing import Callable, Dict, List
from events import BaseEvent, BountyVerifiedEvent

from utils.database import Database
from agents.bounty_radar import BountyRadar
from agents.scam_detector import ScamDetector
from agents.pr_engineer import PREngineer
from agents.code_reviewer import CodeReviewer
from agents.content_engine import ContentEngine
from agents.devops_monitor import DevOpsMonitor
from agents.earnings_tracker import EarningsTracker
from agents.review_tracker import ReviewTracker
from agents.static_analyzer import StaticAnalyzer
from agents.pr_maintainer import PRMaintainer

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("codemechanic.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class EventBus:
    """
    A simple publish-subscribe event bus to decouple agents.
    """
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable):
        """Register a callback for a specific event type."""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)

    def publish(self, event: BaseEvent):
        """Emit an event to all registered subscribers."""
        logger.info(f"[EventBus] Emitting: {event.event_type}")
        if event.event_type in self.subscribers:
            for callback in self.subscribers[event.event_type]:
                try:
                    callback(event.payload)
                except Exception as e:
                    logger.error(f"[EventBus] Error in callback {callback.__name__} for event {event.event_type}: {e}")

import threading

class Orchestrator:
    """
    Main controller for the CodeMechanic-Bot pipeline.
    """
    def __init__(self, config_path: str = "config.yaml", stealth_mode: bool = False):
        self.bus = EventBus()
        self.stealth_mode = stealth_mode
        self.db = Database()
        self.load_config(config_path)
        self.init_agents()
        self.setup_subscriptions()
        self.resume_pending_tasks()
        
        # Synchronization event: True when bounty scan is active
        self.bounty_active_event = threading.Event()
        self.bounty_active_event.clear()

    def resume_pending_tasks(self):
        """Resume any issues that were PENDING if the bot crashed mid-execution."""
        pending = self.db.get_pending_issues()
        if pending:
            logger.info(f"Orchestrator: Found {len(pending)} PENDING issues from a previous run. Resuming...")
            for issue in pending:
                payload = {
                    "issue_url": issue["issue_url"],
                    "repo": issue["repo"],
                    "issue_title": "Resumed Task",
                    "issue_number": issue["issue_url"].split("/")[-1]
                }
                self.bus.publish(BountyVerifiedEvent(payload=payload))

    def load_config(self, config_path: str):
        """Loads configuration settings from a YAML file and .env variables."""
        if os.path.exists(".env"):
            with open(".env", "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        os.environ[key.strip()] = val.strip()
                        
        try:
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f)
            if "github_token" in self.config:
                os.environ["GITHUB_TOKEN"] = self.config["github_token"]
            logger.info("Config loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self.config = {}

    def init_agents(self):
        """Instantiates all agents and injects the publish method."""
        self.radar = BountyRadar(self.bus.publish)
        self.scam_detector = ScamDetector(self.bus.publish)
        self.pr_engineer = PREngineer(self.bus.publish, self.stealth_mode)
        self.reviewer = CodeReviewer(self.bus.publish, self.stealth_mode)
        self.content_engine = ContentEngine(self.bus.publish)
        self.devops_monitor = DevOpsMonitor(self.bus.publish)
        self.earnings_tracker = EarningsTracker(self.bus.publish)
        self.review_tracker = ReviewTracker(self.bus.publish)
        
        # Pass the event to the StaticAnalyzer so it knows when to pause
        self.static_analyzer = StaticAnalyzer(self.bus.publish, self.bounty_active_event)
        self.pr_maintainer = PRMaintainer(self.bus.publish)

    def setup_subscriptions(self):
        """Wires up the event pipeline between agents."""
        self.bus.subscribe("BOUNTY_FOUND", self.scam_detector.evaluate)
        self.bus.subscribe("BOUNTY_VERIFIED", self.pr_engineer.solve_issue)
        self.bus.subscribe("MAINTAINER_FEEDBACK", self.pr_engineer.solve_issue)
        self.bus.subscribe("PR_READY", self.reviewer.review)
        self.bus.subscribe("PR_SUBMITTED", self.content_engine.draft_post)
        self.bus.subscribe("PR_SUBMITTED", self.devops_monitor.track_ci)
        self.bus.subscribe("PR_SUBMITTED", self.earnings_tracker.calculate_roi)
        self.bus.subscribe("PR_REVIEWED", self.pr_engineer.solve_issue)
        self.bus.subscribe("PR_REJECTED", self.pr_engineer.solve_issue)

    def bounty_loop(self):
        """Runs every 30 minutes to scan for bounties."""
        while True:
            logger.info("--- Starting new Bounty Scan Cycle ---")
            self.bounty_active_event.set() # Tell researcher to pause
            
            try:
                self.radar.scan()
                self.pr_maintainer.check_prs()
                self.review_tracker.track()
            except Exception as e:
                logger.error(f"Error in bounty loop: {e}")
                
            self.bounty_active_event.clear() # Allow researcher to resume
            logger.info("--- Bounty Scan Complete. Sleeping for 30 minutes. Researcher unpaused. ---")
            time.sleep(1800) # Sleep 30 minutes

    def researcher_loop(self):
        """Runs continuously, hunting for Zero-Days. Pauses when bounty_active_event is set."""
        while True:
            if self.bounty_active_event.is_set():
                # Pause while bounty scan is running
                time.sleep(10)
                continue
                
            try:
                self.static_analyzer.scan()
            except Exception as e:
                logger.error(f"Error in researcher loop: {e}")
            
            # Small sleep between repos to avoid rate limits
            time.sleep(60)

    def run(self):
        """Starts the orchestrator threads."""
        logger.info("Starting dual-mode CodeMechanic-Bot Orchestrator...")
        
        t_bounty = threading.Thread(target=self.bounty_loop, daemon=True)
        t_researcher = threading.Thread(target=self.researcher_loop, daemon=True)
        
        t_bounty.start()
        t_researcher.start()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nShutting down Orchestrator gracefully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bug Bot Orchestrator")
    parser.add_argument("--stealth", action="store_true", help="Run in stealth mode to mimic a human developer.")
    args = parser.parse_args()
    
    orchestrator = Orchestrator(stealth_mode=args.stealth)
    orchestrator.run()
