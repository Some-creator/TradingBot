"""
Git push helper script.
Usage: python push.py "Your commit message"
"""

import subprocess
import sys
import os

def push(message: str = "Update"):
    """Add, commit, and push changes to GitHub."""
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    print("=== Pushing to GitHub ===")
    
    # Add all changes
    subprocess.run(["git", "add", "-A"], check=True)
    
    # Commit
    result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            print("No changes to commit.")
            return
        else:
            print(result.stderr)
            return
    
    print(result.stdout)
    
    # Push
    result = subprocess.run(
        ["git", "push", "origin", "master"],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        print("✓ Pushed successfully!")
    else:
        print(f"Push failed: {result.stderr}")


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "Update"
    push(msg)
