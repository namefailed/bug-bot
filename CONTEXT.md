# Project Context: CodeMechanic-Bot

## Overview
We are building a 24/7 autonomous Bug Bounty Bot (inspired by the "CodeMechanic-Bot" and "CodeSentinel" articles). The bot scans open source platforms (like GitHub and Algora) for paid bounties, evaluates them, clones the repos, writes fixes using local AI models, tests them in a sandbox, and submits Pull Requests. 

The primary goal is to run this system entirely locally to achieve zero cloud/API costs and maintain full privacy.

## Target Environment
This project is being developed to run on a **fresh Windows 11 installation on a high-end gaming laptop with a beefy GPU**. 
This hardware allows us to run powerful AI models locally (e.g., using Ollama to run Gemma 3 4B or Llama 3 8B/70B) for inference, saving API costs and ensuring code privacy.

## The 7-Agent Orchestration Architecture
The system relies on a central Orchestrator (using an Event Bus pattern) to coordinate 7 specialized agents:

1. **Bounty Radar**: Scans GitHub every 30 minutes for issues labeled "bounty", "reward", etc. Scores them based on competition (age and comments).
2. **Scam Detector**: Evaluates the repository to ensure it's not a trap. (Fake bounties exist to harvest free labor; it checks repo age, merged PR history, and stars).
3. **PR Engineer (Submitter)**: The heavy lifter. Clones the repository and attempts to solve the issue. It uses an **Agent-Computer Interface (ACI)** for safe code navigation and editing, and runs inside a **Docker Desktop Sandbox** to prevent malicious code execution on the host machine.
4. **Code Reviewer (CodeSentinel)**: Reviews the PR Engineer's work locally before submission, checking for SQL injection, performance regressions, and style issues.
5. **Content Engine**: Automatically writes technical blog posts about the bot's findings to build an audience.
6. **DevOps Monitor**: Tracks CI/CD pipelines of submitted PRs to alert if a test fails.
7. **Earnings Tracker**: Monitors payouts and calculates ROI.

## Key Learnings & Guardrails
- **The Speed Game**: Most bounties have a median time-to-first-PR of 47 minutes. The system must act fast.
- **Scam Repositories**: A massive percentage of bounties are fake. Maintain a `blacklist.txt` (e.g., `SecureBananaLabs`, `ClankerNation`).
- **Comment First Strategy**: Before writing code, the bot should propose an approach in the issue comments. If the maintainer approves, it proceeds.
- **Minimal Changes**: The AI should never refactor unrelated code. It must strictly match the project's existing style.

## Instructions for the New Antigravity Instance
Hello, Antigravity! If you are reading this on the new Windows 11 PC, you have been given direct access to this repository to build the system described above. 
Your first tasks should be:
1. Review this `CONTEXT.md` and the `implementation_plan.md` to understand the architecture.
2. Begin building the `orchestrator.py` and the scaffolding for the 7 agents.
3. Ensure Docker Desktop is installed and functioning for the PR Engineer sandbox.
4. Verify Ollama is installed and the chosen models (e.g., `gemma3:4b`) are pulled and responding locally.
