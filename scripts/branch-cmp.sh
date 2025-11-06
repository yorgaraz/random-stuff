#!/bin/bash

if [ "$#" -ne 2 ]; then
    echo "Compares two branches for conflicts and outputs the conflicted files." >&2
    echo "Usage: $0 <base-branch> <branch-to-merge-in>" >&2
    exit 1
fi

readonly BASE_BRANCH="$1"
readonly MERGE_BRANCH="$2"

CURRENT_REPO_PATH=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$CURRENT_REPO_PATH" ]; then
    echo "Error: Not a git repository." >&2
    exit 1
fi

if [[ "$MERGE_BRANCH" == */* ]]; then
    REMOTE_NAME=$(echo "$MERGE_BRANCH" | cut -d'/' -f1)
    
    if [ "$REMOTE_NAME" != "origin" ]; then
        REMOTE_URL=$(git -C "$CURRENT_REPO_PATH" remote get-url "$REMOTE_NAME" 2>/dev/null)
        
        if [ -z "$REMOTE_URL" ]; then
            echo "Error: Could not find URL for remote '$REMOTE_NAME' in original repo." >&2
            exit 1
        fi
    else
        REMOTE_NAME=""
    fi
else
    REMOTE_NAME=""
fi

readonly TEMP_DIR=$(mktemp -d -t git-conflict-check-XXXXXX)
trap "echo; echo 'Cleaning up temp directory...' >&2; rm -rf '$TEMP_DIR'" EXIT

echo "Cloning repo to temp dir..." >&2
git clone "$CURRENT_REPO_PATH" "$TEMP_DIR" 1>&2

echo "Entering temp directory..." >&2
cd "$TEMP_DIR"

echo "Setting 'origin' to no_push..." >&2
git remote set-url --push origin "no_push" 1>&2

if [ -n "$REMOTE_NAME" ]; then
    echo "Adding remote '$REMOTE_NAME'..." >&2
    git remote add "$REMOTE_NAME" "$REMOTE_URL" 1>&2
    
    echo "Setting '$REMOTE_NAME' to no_push..." >&2
    git remote set-url --push "$REMOTE_NAME" "no_push" 1>&2
fi

echo "Fetching all remotes..." >&2
git fetch --all 1>&2

echo "Checking out base branch '$BASE_BRANCH'..." >&2
git checkout --no-track -b temp-branch-for-conflicts "$BASE_BRANCH" 1>&2

echo "" >&2
echo "--- Simulating Merge ---" >&2
echo "Merge branch '$MERGE_BRANCH' into $BASE_BRANCH"
echo "# Conflicts:"

if git merge --no-commit --no-ff "$MERGE_BRANCH" 1>&2; then
    echo "#     No conflicts."
else
    git diff --name-only --diff-filter=U | sed 's/^/#     /'
fi
