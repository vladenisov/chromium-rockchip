#!/bin/sh
# Apply the Debian series and then every Rockchip patch to a fetched source tree, carrying on
# past failures so a port sees all conflicts at once. Rejects are collected in $WORK/rejects.
set -eu
work=$1
version=$(python3 -c 'import json; print(json.load(open("sources.json"))["chromium"]["version"])')
source="$work/source/chromium-$version"
project=$(pwd)
report="$work/patch-report.md"
mkdir -p "$work/rejects"
cp -a "$work/source/debian-bookworm/debian" "$source/debian"
cd "$source"
echo "## Chromium $version" > "$report"
if QUILT_PATCHES=debian/patches quilt push -a -q > "$work/quilt.log" 2>&1; then
    echo "- Debian series: applied" >> "$report"
else
    echo "- Debian series: FAILED, see quilt.log" >> "$report"
    tail -n 20 "$work/quilt.log"
fi
failed=0
for patch in "$project"/patches/chromium/"$version"/*.patch; do
    name=$(basename "$patch")
    if patch --batch -p1 --forward --no-backup-if-mismatch -i "$patch" > "$work/$name.log" 2>&1; then
        fuzz=$(grep -cE 'with fuzz|offset' "$work/$name.log" || true)
        echo "- $name: applied ($fuzz hunks moved)" >> "$report"
    else
        failed=$((failed + 1))
        echo "- $name: **FAILED**" >> "$report"
        grep -E 'FAILED|can.t find file|No file to patch' "$work/$name.log" | sed 's/^/  - /' >> "$report" || true
    fi
done
find . -name '*.rej' -exec sh -c 'for f; do mkdir -p "$0/$(dirname "$f")"; cp "$f" "$0/$f"; done' "$work/rejects" {} +
cat "$report"
test "$failed" -eq 0
