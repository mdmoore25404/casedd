#!/usr/bin/env bash
# Remove generated static HTML files from docs/ and commit the removal to ensure Jekyll builds.
# Run this locally from the repository root.

set -euo pipefail

FILES=(docs/index.html docs/README.html docs/getters.html docs/template_format.html docs/.nojekyll)

# Check for changes first
found=0
for f in "${FILES[@]}"; do
  if [ -e "$f" ]; then
    echo "Removing $f"
    git rm -f "$f"
    found=1
  fi
done

if [ "$found" -eq 0 ]; then
  echo "No generated docs HTML or .nojekyll files found in docs/. Nothing to remove."
  exit 0
fi

git commit -m "chore(docs): remove generated HTML artifacts so Jekyll can render site"

# Push to main
git push origin main

echo "Removed generated files and pushed to origin/main. GitHub Pages should rebuild shortly."
