#!/usr/bin/env bash
# Sync key docs files to the docs/ folder for GitHub Pages
# This script is intentionally local and does not rely on GitHub Actions.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)

# Copy the main README into docs as README.md
cp "$ROOT/README.md" "$ROOT/docs/README.md"

cat > "$ROOT/docs/README-URL.txt" <<'EOF'
Visit your GitHub Pages site:
https://<your-org>.github.io/casedd
EOF

# Build a simple static index page to avoid depending on Jekyll processing.
cat > "$ROOT/docs/index.html" <<'EOF'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CASEDD Documentation</title>
</head>
<body>
<h1>CASEDD Documentation</h1>
<p>This site is served from the <code>docs/</code> folder on the <code>main</code> branch.</p>

<h2>Project README</h2>
<p>The canonical project README is available at <a href="README.md">README.md</a>, and a static pre-generated copy of that content is available at <a href="README.html">README.html</a>.</p>

<h2>User documentation</h2>
<ul>
<li><a href="getters.html">Getter key reference</a> (from <code>docs/getters.md</code>)</li>
<li><a href="template_format.html">Template format reference</a> (from <code>docs/template_format.md</code>)</li>
</ul>

<p>Issue <strong>#20</strong> is closed by documenting this setup and serving all docs as static pages.</p>
</body>
</html>
EOF

# Create simple HTML shims for docs/viewability under nojekyll.
for mdfile in README getters template_format; do
  out="$ROOT/docs/${mdfile}.html"
  echo "<!doctype html>" > "$out"
  echo "<html lang=\"en\">" >> "$out"
  echo "<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>CASEDD ${mdfile}</title></head>" >> "$out"
  echo "<body><h1>CASEDD ${mdfile}</h1><p>Source: <a href=\"${mdfile}.md\">${mdfile}.md</a></p>" >> "$out"
  echo "<pre style=\"white-space: pre-wrap; font-family: monospace;\">" >> "$out"
  sed 's/&/&amp;/g; s/</\&lt;/g; s/>/\&gt;/g;' "$ROOT/docs/${mdfile}.md" >> "$out" 2>/dev/null || true
  echo "</pre></body></html>" >> "$out"
done

echo "Synced README.md to docs/README.md and ensured static pages in docs/"
