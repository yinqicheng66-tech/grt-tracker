#!/usr/bin/env bash
# Export the dashboard data and publish web/ plus data.json to the gh-pages
# branch, which GitHub Pages serves. Runs after each hourly ingest.
#
# One-time setup on the server: a deploy key with write access to this repo
# (see README), then run this script once by hand.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SITE_DIR="${SITE_DIR:-$HOME/grt-site}"
REMOTE="${PAGES_REMOTE:-git@github.com:yinqicheng66-tech/grt-tracker.git}"
GIT=(git -C "$SITE_DIR" -c user.name="grt-tracker bot" -c user.email="grt-tracker@users.noreply.github.com")

cd "$REPO_DIR"
python3 -m pipeline.export --db data/grt.db --out data/site/data.json

if [ ! -d "$SITE_DIR/.git" ]; then
  mkdir -p "$SITE_DIR"
  git -C "$SITE_DIR" init -q
  git -C "$SITE_DIR" checkout -q -b gh-pages
  git -C "$SITE_DIR" remote add origin "$REMOTE"
fi

cp web/* "$SITE_DIR"/
cp data/site/data.json "$SITE_DIR"/
touch "$SITE_DIR/.nojekyll"

"${GIT[@]}" add -A
message="Dashboard data $(date -u +%Y-%m-%dT%H:%MZ)"
# The branch keeps a single commit holding only the latest snapshot, so the
# repository does not grow by a copy of data.json every hour.
if "${GIT[@]}" rev-parse -q --verify HEAD >/dev/null; then
  "${GIT[@]}" commit -q --amend -m "$message"
else
  "${GIT[@]}" commit -q -m "$message"
fi
"${GIT[@]}" push -q -f origin gh-pages
echo "published $message"
