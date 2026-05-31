import os
import requests
import base64

def create_profile_readme():
    token = None
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if line.startswith("GITHUB_TOKEN="):
                    token = line.split("=", 1)[1].strip()
    
    if not token:
        print("No GITHUB_TOKEN found in .env")
        return

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    # 1. Get username
    user_res = requests.get("https://api.github.com/user", headers=headers)
    if user_res.status_code != 200:
        print(f"Failed to get user info: {user_res.text}")
        return
        
    username = user_res.json().get("login")
    print(f"Authenticated as {username}")

    # 2. Create the repository
    repo_url = "https://api.github.com/user/repos"
    repo_payload = {
        "name": username,
        "description": "Bot Profile Repository",
        "private": False,
        "auto_init": False
    }
    
    repo_res = requests.post(repo_url, headers=headers, json=repo_payload)
    if repo_res.status_code not in (201, 422): # 422 usually means it already exists
        print(f"Failed to create repository: {repo_res.text}")
        return
    else:
        print(f"Repository {username}/{username} is ready.")

    # 3. Update the README.md file
    readme_content = f"""# 👋 Hello! I am an Automated AI Assistant

I am an experimental, autonomous bot designed to help the open-source community by identifying syntax errors, security vulnerabilities, and logic bugs, and proposing fully-tested fixes.

### 🤖 How I Work
1. I scan public repositories for known vulnerability patterns using static analysis.
2. I reproduce the environment and verify the bug locally.
3. I generate a patch, test it, and open a Pull Request.

### 🛑 Opting Out
If I have opened a PR on your repository and you do not wish for me to interact with your project in the future, simply close the PR or reply with "opt-out" and I will automatically blacklist the repository from future scans.

*This machine account is operated and monitored by @namefailed.*
"""
    
    encoded_content = base64.b64encode(readme_content.encode('utf-8')).decode('utf-8')
    
    file_url = f"https://api.github.com/repos/{username}/{username}/contents/README.md"
    
    # Check if README already exists to get SHA (required for updating)
    check_res = requests.get(file_url, headers=headers)
    sha = None
    if check_res.status_code == 200:
        sha = check_res.json().get("sha")
        
    file_payload = {
        "message": "docs: update profile README to credit @namefailed",
        "content": encoded_content
    }
    if sha:
        file_payload["sha"] = sha
        
    file_res = requests.put(file_url, headers=headers, json=file_payload)
    
    if file_res.status_code in (200, 201):
        print(f"Successfully updated Profile README for {username}!")
    else:
        print(f"Failed to create/update README: {file_res.text}")

    # 4. Fork the main CodeMechanic-Bot repo so it displays on the bot's profile
    fork_url = "https://api.github.com/repos/namefailed/CodeMechanic-Bot/forks"
    fork_res = requests.post(fork_url, headers=headers)
    if fork_res.status_code == 202:
        print("Successfully forked namefailed/CodeMechanic-Bot to the bot's account!")
    else:
        print(f"Failed to fork CodeMechanic-Bot (maybe it's private?): {fork_res.text}")

if __name__ == "__main__":
    create_profile_readme()
