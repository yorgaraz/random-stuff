#!/bin/bash

commit_sha=$(git rev-parse HEAD)
echo -n "$commit_sha" | xclip -selection clipboard
echo "Commit SHA $commit_sha has been copied to the clipboard."

