#!/usr/bin/env python3
"""
Auto Git Updater Script
Automatically commits and pushes changes to the remote repository.
Syncs deletions - removes files from remote that don't exist locally.
"""

import subprocess
import sys
from datetime import datetime


def run_command(cmd: list[str], check: bool = True, verbose: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    if verbose:
        print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}")
        if result.stdout:
            print(f"Output: {result.stdout}")
    return result


def sync_deletions():
    """
    Remove files from git that no longer exist locally.
    This ensures the remote matches our local directory.
    """
    print("\n--- Syncing deletions ---")

    # Get list of files tracked by git
    result = run_command(["git", "ls-files"], check=False, verbose=False)
    if result.returncode != 0:
        return

    tracked_files = result.stdout.strip().split('\n')
    if not tracked_files or tracked_files == ['']:
        return

    deleted_files = []

    # Check each tracked file
    for filepath in tracked_files:
        if not filepath:
            continue
        # Use git ls-files to check if file exists in working tree
        check_result = run_command(
            ["git", "ls-files", "--error-unmatch", filepath],
            check=False,
            verbose=False
        )

        # Also check with Python if file actually exists on disk
        import os
        if not os.path.exists(filepath):
            deleted_files.append(filepath)

    if deleted_files:
        print(f"Found {len(deleted_files)} deleted file(s):")
        for f in deleted_files:
            print(f"  - {f}")

        # Remove deleted files from git index
        for filepath in deleted_files:
            run_command(["git", "rm", "--cached", filepath], check=False, verbose=False)

        print(f"Removed {len(deleted_files)} file(s) from git tracking")
    else:
        print("No deleted files to sync")


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

    # Sync deletions - remove files from git that don't exist locally
    sync_deletions()

    # Get current status (including deletions)
    result = run_command(["git", "status", "--porcelain"], check=False)

    if not result.stdout.strip():
        print("\nNo changes to commit")
        # Still try to push in case there are unpushed commits
    else:
        print(f"\nChanges detected:\n{result.stdout}")

        # Stage ALL changes including deletions
        # -A stages new files, modifications, AND deletions
        run_command(["git", "add", "-A"])

        # Create commit message with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_msg = f"Auto-update: {timestamp}"

        # Check if custom message provided (exclude flags)
        args = [arg for arg in sys.argv[1:] if arg not in ("--force", "-f")]
        if args:
            commit_msg = " ".join(args)

        # Commit
        result = run_command(["git", "commit", "-m", commit_msg], check=False)
        if result.returncode != 0:
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                print("Nothing to commit")
            else:
                print(f"Commit failed: {result.stderr}")

    # Push to remote (main branch only)
    print("\nPushing to origin/main...")

    # Check for --force flag
    force_push = "--force" in sys.argv or "-f" in sys.argv

    if force_push:
        result = run_command(["git", "push", "-u", "origin", "main", "--force"], check=False)
    else:
        result = run_command(["git", "push", "-u", "origin", "main"], check=False)

    if result.returncode != 0:
        print(f"Push failed: {result.stderr}")
        print("\nTip: Use --force or -f flag to force push if branches diverged")
        sys.exit(1)

    print("\nSuccessfully pushed to remote!")

    # Show latest commit
    run_command(["git", "log", "-1", "--oneline"])

    # Show summary
    print("\n--- Summary ---")
    result = run_command(["git", "status", "--short"], check=False, verbose=False)
    if result.stdout.strip():
        print(f"Remaining uncommitted changes:\n{result.stdout}")
    else:
        print("Working tree clean - fully synced with remote!")


if __name__ == "__main__":
    main()
