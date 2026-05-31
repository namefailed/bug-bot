import os
import requests
import time

def main():
    with open('c:/Users/grey/Projects/dev/CodeMechanic-Bot/.env', 'r') as f:
        for line in f:
            if line.startswith('GITHUB_TOKEN='):
                token = line.strip().split('=', 1)[1]

    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'Authorization': f'token {token}'
    }

    user = requests.get('https://api.github.com/user', headers=headers).json().get('login')
    if not user:
        print("Failed to get user")
        return
    print(f'User: {user}')

    q = f'commenter:{user} is:issue updated:>=2026-05-29'
    res = requests.get('https://api.github.com/search/issues', headers=headers, params={'q': q}).json()
    issues = res.get('items', [])
    print(f"Found {len(issues)} issues where user commented.")
    
    bot_messages = [
        "Hey! I was just looking through the codebase and noticed this issue. Taking a stab at fixing it now, I'll send over a PR if I get it working!",
        "Hi! I've analyzed the issue and identified the root cause. I'm preparing a minimal fix with tests matching the repo's style. I will submit a PR shortly."
    ]

    # Get open PRs by user to close them
    pr_q = f"is:pr author:{user} is:open"
    pr_res = requests.get('https://api.github.com/search/issues', headers=headers, params={'q': pr_q}).json()
    open_prs = pr_res.get('items', [])
    print(f"Found {len(open_prs)} open PRs by user.")
    
    for pr in open_prs:
        print(f"Closing PR {pr['html_url']}...")
        requests.patch(pr['url'], headers=headers, json={'state': 'closed'})

    for issue in issues:
        comments_url = issue['comments_url']
        issue_number = issue['number']
        
        # Get all comments on this issue
        comments_res = requests.get(comments_url, headers=headers).json()
        
        # Filter comments made by the bot user
        user_comments = [c for c in comments_res if c.get('user', {}).get('login') == user and c.get('body') in bot_messages]
        
        if not user_comments:
            continue
            
        print(f"\nIssue: {issue['html_url']}")
        print(f"Found {len(user_comments)} bot comments. Deleting all...")
        
        for comment in user_comments:
            del_url = comment['url']
            d_res = requests.delete(del_url, headers=headers)
            if d_res.status_code == 204:
                print(f"Deleted comment {comment['id']}")
            else:
                print(f"Failed to delete {comment['id']}: {d_res.status_code} {d_res.text}")
                
        time.sleep(1) # rate limiting

if __name__ == '__main__':
    main()
