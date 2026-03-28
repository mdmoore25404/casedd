#!/usr/bin/env bash
# Sync key docs files to the docs/ folder for GitHub Pages
# This script is intentionally local and does not rely on GitHub Actions.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)

# Copy the main README into docs as README.md (and optionally keep any site layout)
cp "$ROOT/README.md" "$ROOT/docs/README.md"

cat > "$ROOT/docs/.nojekyll" <<'EOF'
# No Jekyll processing for raw static site files.
EOF

cat > "$ROOT/docs/README-URL.txt" <<'EOF'
Visit your GitHub Pages site:
https://<your-org>.github.io/casedd
EOF

echo "Synced README.md to docs/README.md and ensured docs/.nojekyll"
