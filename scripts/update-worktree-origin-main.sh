#!/usr/bin/env bash

set -euo pipefail

if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
  echo "This script must be run inside a git worktree." >&2
  exit 1
fi

if ! git remote get-url origin > /dev/null 2>&1; then
  echo "Remote 'origin' is not configured." >&2
  exit 1
fi

current_branch="$(git branch --show-current)"

if [[ -n "$current_branch" ]]; then
  current_ref_description="$current_branch"
else
  current_ref_description="detached HEAD at $(git rev-parse --short HEAD)"
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Worktree has uncommitted changes. Commit or stash them first." >&2
  exit 1
fi

echo "Fetching origin/main..."
git fetch --prune origin main

if git merge-base --is-ancestor origin/main HEAD; then
  echo "${current_ref_description} already contains origin/main."
  exit 0
fi

echo "Rebasing ${current_ref_description} onto origin/main..."
git rebase origin/main

echo "Worktree updated to the latest origin/main."
git status --short --branch
