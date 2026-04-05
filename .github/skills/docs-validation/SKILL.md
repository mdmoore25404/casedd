---
name: docs-validation
description: '**WORKFLOW SKILL** — Validate CASEDD documentation pages at localhost:4000 against actual codebase source, find inaccuracies, fix markdown docs. USE FOR: after adding/changing getters, templates, CLI commands, or API; periodic doc accuracy audits; after any large refactor; before releases. DO NOT USE FOR: creating new feature docs (write alongside the code); changelog or release notes; README edits unrelated to reference pages. INVOKES: run_in_terminal (start Jekyll, fetch pages, compare), read_file + grep_search (read source), replace_string_in_file (fix docs).'
---

# CASEDD Docs Validation Workflow

This skill validates that the Jekyll-rendered documentation pages at `http://localhost:4000`
accurately reflect the actual codebase, then fixes any inaccuracies in the markdown source.

---

## Step 1 — Start the Jekyll server

```bash
# Check if already running
docker ps --filter "name=casedd-pages" --format "{{.Status}}"

# If not running, start it
./dev.sh pages &>/tmp/jekyll.log &

# Poll until ready (typically ~10 s)
until curl -s http://localhost:4000/ -o /dev/null -w "%{http_code}" | grep -q "200"; do sleep 2; done
echo "Jekyll ready"
```

Pages are served at `http://localhost:4000`. The container name is `casedd-pages-dev`.

---

## Step 2 — Fetch all doc pages

```bash
curl -s http://localhost:4000/getters/         -o /tmp/getters.html
curl -s http://localhost:4000/api/             -o /tmp/api.html
curl -s http://localhost:4000/template_format/ -o /tmp/template_format.html
curl -s http://localhost:4000/cli/             -o /tmp/cli.html
curl -s http://localhost:4000/                 -o /tmp/index.html
```

---

## Step 3 — Extract text content

Use this Python snippet to strip navigation/script/footer noise and get plain text:

```python
from html.parser import HTMLParser
import re

class TextExtract(HTMLParser):
    SKIP = {'script', 'style', 'nav', 'header', 'footer'}
    def __init__(self):
        super().__init__()
        self.skip = 0; self.buf = []
    def handle_starttag(self, t, a):
        if t in self.SKIP: self.skip += 1
    def handle_endtag(self, t):
        if t in self.SKIP and self.skip > 0: self.skip -= 1
    def handle_data(self, d):
        if not self.skip: self.buf.append(d)
    def text(self):
        return re.sub(r'\n{3,}', '\n\n', ''.join(self.buf)).strip()

for name, path in [('getters', '/tmp/getters.html'), ('cli', '/tmp/cli.html'),
                   ('template_format', '/tmp/template_format.html'), ('api', '/tmp/api.html')]:
    p = TextExtract()
    p.feed(open(path).read())
    print(f"=== {name.upper()} ===")
    print(p.text()[:6000])
    print()
```

---

## Step 4 — Source files to validate against

| Documentation page | Source files to compare |
|--------------------|------------------------|
| `/getters/` | `casedd/getters/*.py` (module docstrings + emitted keys), `casedd/config.py` (env vars) |
| `/api/` | `docs/api.json` (OpenAPI snapshot), `casedd/outputs/http_viewer.py` |
| `/template_format/` | `casedd/template/models.py`, `casedd/renderer/widgets/*.py` |
| `/cli/` | `casedd/cli.py` (argparse commands), `casedd-ctl` |

---

## Step 5 — Extract actual keys from getter source

```bash
python3 - <<'EOF'
import os, re

getters_dir = 'casedd/getters'
for fname in sorted(os.listdir(getters_dir)):
    if not fname.endswith('.py') or fname.startswith('_') or fname == 'base.py':
        continue
    src = open(f'{getters_dir}/{fname}').read()
    keys = sorted(set(re.findall(r'"([a-z][a-z0-9_]*\.[a-z0-9_.]+)"', src)))
    print(f"=== {fname} ===")
    for k in keys:
        print(f"  {k}")
EOF
```

Also extract env vars from `casedd/config.py`:

```bash
grep "CASEDD_[A-Z_]*.*_get\|_get.*CASEDD_" casedd/config.py | head -60
```

---

## Step 6 — Common inaccuracy patterns to check

1. **Missing emitted keys** — compare the getter's `fetch()` dict literals against the docs key list.
2. **Wrong keys** — docs list keys that don't exist in code (e.g. were renamed or removed).
3. **Missing getter sections** — run `ls casedd/getters/*.py` and compare against docs `## headings`.
4. **Wrong CLI commands** — compare argparse `add_parser("name")` calls in `casedd/cli.py` against docs.
5. **Misplaced content** — content from one section appearing under another section heading.
6. **Stale env var names** — env var names in docs vs `casedd/config.py` `_get("CASEDD_...", ...)` calls.

---

## Step 7 — Fix docs and verify

Edit `docs/getters.md`, `docs/cli.md`, `docs/template_format.md`, or `docs/api.md` as needed.
Jekyll regenerates automatically — re-fetch the affected page and spot-check the output:

```bash
curl -s http://localhost:4000/getters/ | python3 -c "
import sys, re
from html.parser import HTMLParser
class T(HTMLParser):
    def __init__(self): super().__init__(); self.skip=0; self.b=[]
    def handle_starttag(self,t,a):
        if t in {'script','style','nav','header','footer'}: self.skip+=1
    def handle_endtag(self,t):
        if t in {'script','style','nav','header','footer'} and self.skip: self.skip-=1
    def handle_data(self,d):
        if not self.skip: self.b.append(d)
p=T(); p.feed(sys.stdin.read())
text=re.sub(r'\n{3,}','\n\n',''.join(p.b))
for check in ['nvidia.name','system.boot_time','actionable_count','TrueNAS getter']:
    print('✓' if check in text else '✗ MISSING', check)
"
```

---

## Getter docs section template

When adding a new getter section to `docs/getters.md`, use this template:

````markdown
## <Name> getter

Module: casedd/getters/<module>.py

Config:
- `CASEDD_<NAME>_<FIELD>` — description (default: `<value>`)

Emits:
- <namespace>.<key>

Notes:
- Key gotcha or behavior note.
````

Place new sections before `## Template-aware polling` at the bottom of the file.
