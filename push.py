#!/usr/bin/env python3
"""
Auto Git Updater Script
Automatically commits and pushes changes to the remote repository.
"""

import subprocess
import sys
from datetime import datetime


def run_command(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}")
        if result.stdout:
            print(f"Output: {result.stdout}")
    return result


def main():
    # Check if we're in a git repo
    result = run_command(["git", "rev-parse", "--is-inside-work-tree"], check=False)
    if result.returncode != 0:
        print("Error: Not a git repository")
        sys.exit(1)

    # Check for remote
    result = run_command(["git", "remote", "-v"], check=False)
    if "origin" not in result.stdout:
        print("Adding remote origin...")
        run_command([
            "git", "remote", "add", "origin",
            "https://github.com/Some-creator/TradingBot.git"
        ])
    else:
        # Update remote URL if needed
        run_command([
            "git", "remote", "set-url", "origin",
            "https://github.com/Some-creator/TradingBot.git"
        ], check=False)

    # Get current status
    result = run_command(["git", "status", "--porcelain"], check=False)

    if not result.stdout.strip():
        print("No changes to commit")
        # Still try to push in case there are unpushed commits
    else:
        print(f"Changes detected:\n{result.stdout}")

        # Stage all changes
        run_command(["git", "add", "-A"])

        # Create commit message with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_msg = f"Auto-update: {timestamp}"

        # Check if custom message provided
        if len(sys.argv) > 1:
            commit_msg = " ".join(sys.argv[1:])

        # Commit
        result = run_command(["git", "commit", "-m", commit_msg], check=False)
        if result.returncode != 0:
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                print("Nothing to commit")
            else:
                print(f"Commit failed: {result.stderr}")

    # Push to remote
    print("\nPushing to origin/master...")
    result = run_command(["git", "push", "-u", "origin", "master"], check=False)

    if result.returncode != 0:
        # Try main branch if master fails
        print("Trying origin/main...")
        result = run_command(["git", "push", "-u", "origin", "main"], check=False)

        if result.returncode != 0:
            print(f"\nPush failed. You may need to:")
            print("1. Set up authentication: git config credential.helper store")
            print("2. Or use SSH: git remote set-url origin git@github.com:Some-creator/TradingBot.git")
            print(f"\nError: {result.stderr}")
            sys.exit(1)

    print("\nSuccessfully pushed to remote!")

    # Show latest commit
    run_command(["git", "log", "-1", "--oneline"])


if __name__ == "__main__":
    main()
