#!/bin/sh
# Print the newest successful Build run whose browser archive this commit can reuse: only files
# that cannot affect the compiled browser differ. A file not listed here counts as a build input,
# so a new file rebuilds until it is added. Prints nothing when a build is needed.
set -eu
head=${1:-HEAD}
gh run list --workflow build.yml --status success --limit 50 --json databaseId,headSha,displayTitle \
    --jq '.[] | select(.displayTitle | startswith("Build (resumed") | not) | "\(.databaseId) \(.headSha)"' |
while read -r run sha; do
    git diff --quiet "$sha" "$head" -- . ':!*.md' ':!LICENSE' ':!.gitignore' ':!Makefile' \
        ':!test_*.py' ':!install.py' ':!verify-browser.py' ':!requirements-verify.txt' \
        ':!check-patches.sh' ':!find-reusable-build.sh' ':!.github/release-notes.md' \
        ':!.github/workflows/build.yml' ':!.github/workflows/checks.yml' \
        ':!.github/workflows/patch-check.yml' 2>/dev/null || continue
    browser=$(gh api "repos/{owner}/{repo}/actions/runs/$run/artifacts" \
        --jq '.artifacts[] | select(.name == "browser" and (.expired | not)) | .name')
    if [ -n "$browser" ]; then
        echo "$run"
        exit 0
    fi
done
