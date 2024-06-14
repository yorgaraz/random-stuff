#!/bin/bash

if [ "$#" -ne 2 ]; then
    echo "Compare two branches for conflicts and outputs the conflicted files in the style of sourcetree merge commit message."
    echo "Usage: $0 <branch1> <branch2>"
    exit 1
fi

BRANCH1="$1"
BRANCH2="$2"

git fetch --all &> /dev/null

git checkout "$BRANCH1" &> /dev/null
git pull origin "$BRANCH1" &> /dev/null

git checkout "$BRANCH2" &> /dev/null
git pull origin "$BRANCH2" &> /dev/null

git checkout --no-track -b temp-branch-for-conflicts "$BRANCH1" &> /dev/null

echo "Merge branch '$BRANCH1' into $BRANCH2"
echo "# Conflicts:"

if git merge --no-commit --no-ff "$BRANCH2" &> /dev/null; then
    echo "    # No conflicts between $BRANCH1 and $BRANCH2."
else
    git diff --name-only --diff-filter=U | sed 's/^/#     /'
    git merge --abort &> /dev/null
fi

git checkout "$BRANCH1" &> /dev/null
git branch -D temp-branch-for-conflicts &> /dev/null

