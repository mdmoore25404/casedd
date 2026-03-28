#!/usr/bin/env bash
# Sync key docs files to the docs/ folder for GitHub Pages
# This script is intentionally local and does not rely on GitHub Actions.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)

# Copy the main README into docs as README.md
cp "$ROOT/README.md" "$ROOT/docs/README.md"

# Ensure Jekyll processing remains enabled by removing the nojekyll marker.
rm -f "$ROOT/docs/.nojekyll"

# Remove previously generated static HTML artifacts that conflict with Jekyll
rm -f "$ROOT/docs/index.html" "$ROOT/docs/README.html" "$ROOT/docs/getters.html" "$ROOT/docs/template_format.html"

cat > "$ROOT/docs/README-URL.txt" <<'EOF'
Visit your GitHub Pages site:
https://<your-org>.github.io/casedd
EOF

echo "Synced README.md to docs/README.md and Jekyll site mode enabled (no .nojekyll)"
