# CASEDD — GitHub Copilot Agentic Guidelines

## Project overview

**CASEDD** is the Case Display Daemon — a lightweight, high-performance Python daemon that
drives a small USB framebuffer display (Waveshare 5-inch, 800×480) while simultaneously
serving the same content over WebSocket and HTTP for remote viewing.

Stack: Python 3.12, FastAPI, uvicorn, Pillow (PIL), Pydantic v2 strict, psutil, PyYAML.

---

## Mandatory coding rules

### General

- **No local (just-in-time) imports.** All imports must be at the top of the module.
- **No lazy typing.** Every function parameter, return value, and class attribute MUST have
  a type annotation. `Any` is forbidden unless wrapping an untyped third-party boundary and
  immediately cast. Use `object` when a truly generic type is needed.
- **No linting errors.** `ruff check .` and `mypy --strict` must both pass with zero
  issues. Iterate until clean; escalate to human only when a rule conflict is unresolvable.
- **f-strings preferred** for all string formatting. `.format()` and `%`-style formatting
  are only acceptable inside third-party library calls that require them.
- **Lightweight and high-performance.** Avoid unnecessary allocations in hot paths
  (render loop, WebSocket broadcast). Profile before optimising, but never add bloat.
- **Always add automated tests for changes.** New behaviour and bug fixes must ship with
  unit tests and, when the boundary is externally observable, integration tests as well.
  If a true integration test is impractical, explain why and add the closest automated
  coverage possible before considering the task complete.

### Lint anti-pattern blacklist (must avoid while generating code)

Use this section as a pre-flight checklist during implementation, not only at cleanup time.

- **Do not exceed line length limits.** Keep lines <= 99 chars in Python and avoid long
  single-line string literals inside embedded HTML/JS blocks.
- **Do not omit trailing newline at EOF.** New/rewritten files must end with a newline.
- **Do not use unnecessary temporary return variables** (Ruff `RET504`).
  Return expressions directly unless intermediate variables materially improve clarity.
- **Do not create high-return-count helper functions** (Ruff `PLR0911`) when a small
  dispatch table or loop can express the flow more cleanly.
- **Do not create argument-heavy private helpers** (Ruff `PLR0913`) in render paths
  unless unavoidable. Prefer a small context object/dataclass, or keep helper logic local.
- **Do not add blanket `# noqa` / `# type: ignore`.** If suppression is truly needed,
  use the narrowest code and include a reason on the same line.
- **Do not over-constrain update payload models** when payload normalization is expected.
  For nested update flattening, accept `dict[str, object]` at the boundary and coerce
  to store primitives after validation.
- **Do not rely on final-pass linting only.** Run `ruff check .` and `mypy --strict casedd/`
  immediately after significant file edits (or per subsystem) to catch issues early.
- **Do not use `▲` (U+25B2) for descending sort indicators.** Descending order (highest
  first) should use `▼` (U+25BC) — wide side at top, pointing down. `▲` implies ascending.
- **Do not forget to cast PIL `textbbox()` results to `int`.**  `ImageDraw.textbbox()`
  returns `tuple[float, float, float, float]`.  All arithmetic on the result (differences,
  sums used as pixel indices or passed to `int`-typed params) must be wrapped in `int()`.
  Example: `int(bb[3] - bb[1]) + 3`. Skipping the cast causes `mypy --strict` errors.
- **Do not use `is None` to narrow psutil `laddr`/`raddr`.**  These are typed
  `addr | tuple[()]`, not `addr | None`.  Use a falsy check: `if not laddr: continue`.
  Using `if laddr is None:` fails `mypy --strict` narrowing since `tuple[()]` is not None.
- **Do not add imports without running `ruff check . --fix` immediately.**  Ruff enforces
  isort ordering (I001) and will flag out-of-order imports.  After adding any `import`
  statement, run `ruff check . --fix` to auto-correct ordering before moving on.
 - **Do not use axios for JavaScript/TypeScript API calls.** All JavaScript/TypeScript
   API requests must use the native `fetch` API (or the platform's fetch polyfill).
   Adding `axios` as a dependency or introducing code that uses it is disallowed.
- **Do not write PLR0915-violating functions** (too many statements, limit ≈ 50).  For
  table/widget renderers, always extract `_paint_header`, `_paint_rows`, `_paint_row`
  helpers rather than inlining all drawing logic into `draw()`.
- **Do not trigger S104 false positives on address comparisons.**  Comparing a string
  variable against wildcard address literals (`"0.0.0.0"`, `"::"`) triggers Ruff S104
  ("binding to all interfaces"). Suppress with `# noqa: S104  # string compare, not bind`.- **Do not position PIL text by the draw origin without correcting for bbox offsets.**
  `draw.text((x, y), text, font=font)` places the font **origin** at `(x, y)`.  The visible
  glyph actually renders from `y + bbox[1]` to `y + bbox[3]` (and `x + bbox[0]` to
  `x + bbox[2]`).  Centring by `th = bbox[3] - bbox[1]` alone shifts glyphs downward and
  causes bottom-of-cell overflow at large font sizes.  Always correct:  
  `y_draw = y_target - bbox[1]` and `x_draw = x_target - bbox[0]`.
- **Do not hard-cap font sizes at display-agnostic magic numbers.**  Small integer caps like
  `min(16, ...)` or fixed `get_font(13)` become invisible at 4K / large panel resolutions.
  Scale proportionally: `max(14, rect.w // 55)` or similar formula, with no upper cap for
  full-screen widgets.
- **Do not hardcode font sizes in templates or widget defaults.** All font sizing must be
  dynamic, calculated based on the widget's bounding box (width, height) and optionally
  padding. Use `font_size: "auto"` in `.casedd` templates and implement proportional
  scaling logic in the widget's `draw()` method. Example: for a 100-pixel wide label with
  5px padding, scale the font to fit `(100 - 10) = 90` pixels. This ensures widgets adapt to
  all display resolutions (800×480, 1024×600, 4K) without requiring per-resolution tuning.
- **Do not use fixed font-size floors for wrapped/multiline text** (for example hardcoded
  minima like `8`/`10` or fixed `+2` line spacing). Compute minimum size and line gap from
  the widget container dimensions so dense lists remain readable across resolutions.
### Self-learning anti-pattern protocol

When the agent encounters a new ruff or mypy violation during an implementation session
that is **not already listed above**, it must:

1. Fix the violation in the code.
2. Add a concise bullet point to the "Lint anti-pattern blacklist" above, following the
   established format: bold summary + rule code (if applicable) + one-line fix strategy.
3. Commit the instruction update together with (or immediately after) the code fix so the
   knowledge is captured for future sessions.

This keeps the blacklist as a live, self-improving knowledge base rather than a static snapshot.

### Python specifics

- Python version: **3.12**. Use modern syntax: `X | Y` unions, `match` statements,
  `TypeAlias`, `TypeVar`, `ParamSpec`, `dataclasses` where Pydantic is overkill.
- Pydantic v2 strict models for all config, template, and API request/response types.
  Set `model_config = ConfigDict(strict=True, frozen=True)` on data models.
- `asyncio` for concurrency. No `threading` except where a blocking C extension forces it
  (e.g. PIL render — wrap in `asyncio.to_thread`).
- Use `logging` stdlib. Never use `print()` for operational output. Log levels: DEBUG for
  per-frame detail, INFO for lifecycle events, WARNING for degraded state, ERROR for
  non-fatal failures, CRITICAL for unrecoverable errors.
- All file I/O uses `pathlib.Path`. Never concatenate path strings with `+` or `/`.

### Documentation

- Every **module** must have a module-level docstring explaining its purpose and public API.
- Every **public function, method, and class** must have a docstring. Use Google-style
  docstrings (Args, Returns, Raises sections). This documents for both humans and agents.
- Every **non-obvious line or block** gets an inline comment explaining *why*, not *what*.
- The `.casedd` template format must be documented in `docs/template_format.md`.
- API docs are generated locally by running `./dev.sh docs` (calls `scripts/gen_docs.sh`).
  Never generate docs in GitHub Actions.

---

## Project structure

```
casedd/                  # Top-level Python package
  __init__.py
  __main__.py            # python -m casedd entry point, PID file management
  config.py              # Pydantic config model, YAML + env var loading
  daemon.py              # Main async orchestrator
  logging_setup.py       # Rotating file + console handlers, color formatter
  data_store.py          # Thread-safe in-RAM key/value store
  getters/               # Data source pollers
    __init__.py
    base.py              # Abstract BaseGetter
    cpu.py
    gpu.py               # nvidia-smi, graceful no-op if absent
    memory.py
    disk.py
    network.py
    system.py
  template/              # .casedd template engine
    __init__.py
    loader.py            # Parse .casedd YAML → Template model
    grid.py              # CSS Grid Template Areas → pixel bounding boxes
    models.py            # Pydantic models for all widget types
    registry.py          # Template name → Template, hot-reload
  renderer/              # PIL image generation
    __init__.py
    engine.py            # render(template, data_store) → PIL.Image
    fonts.py             # Font loader, auto-scale to bounding box
    color.py             # Hex/RGB/gradient, color_stops interpolation
    widgets/             # One module per widget type
      __init__.py
      base.py            # Abstract BaseWidget.draw(img, rect, cfg, data)
      panel.py
      value.py
      text.py
      bar.py
      gauge.py
      histogram.py
      sparkline.py
      image.py
      slideshow.py
      clock.py
  outputs/               # Output sinks
    __init__.py
    framebuffer.py       # mmap write to /dev/fb1, CASEDD_NO_FB fallback
    websocket.py         # FastAPI WebSocket broadcast
    http_viewer.py       # FastAPI HTTP app, /docs, /image
  ingestion/             # Data write receivers
    __init__.py
    unix_socket.py       # Unix domain socket JSON receiver
    rest.py              # FastAPI POST /update endpoint
templates/               # .casedd template files (user-editable)
assets/                  # Static assets
  slideshow/             # Images for slideshow template
deploy/                  # Deployment files
  casedd.service         # systemd unit
  install/
    install.sh
docs/                    # Generated and hand-written docs
  api.json               # Generated by scripts/gen_docs.sh
  template_format.md     # .casedd format spec
scripts/
  gen_docs.sh            # Local API doc generation (not CI)
run/                     # PID files (dev, gitignored)
logs/                    # Log files (dev, gitignored)
dev.sh                   # Dev workflow script
docker-compose.yml
Dockerfile
.env.example
pyproject.toml
requirements.txt
requirements-dev.txt
README.md
```

---

## Branch and commit discipline

- **Never commit to `main` directly.** Always create a branch from `main` named
  `issue/<number>-<short-slug>` (e.g. `issue/3-core-daemon`).
- Commit messages: `<type>(<scope>): <imperative summary>` — e.g.
  `feat(renderer): add gauge widget with color_stops`.
  Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`.
- One logical change per commit. Do not batch unrelated changes.
- Open a PR to `main` when the issue's acceptance criteria are all met.
- For interactive human requests, once implementation is complete and validation
  passes (`ruff check .` and `mypy --strict casedd/`, plus tests when present),
  the agent should create a commit before concluding the request.

---

## Template format (.casedd)

Templates are YAML files with a `.casedd` extension. Full spec in `docs/template_format.md`.

Key rules:
- `grid.template_areas` uses CSS Grid Template Areas syntax. Repeated widget name = span.
- `grid.columns` and `grid.rows` accept `fr`, `px`, or `%` units.
- Widget `source` is a dotted data store key (e.g. `cpu.temperature`).
- Widget `content` is a literal string (static text, no data store lookup).
- `type: panel` widgets may have `children` — enabling unlimited nesting.
- Widget types MVP: `panel`, `value`, `text`, `bar`, `gauge`, `histogram`, `sparkline`,
  `image`, `slideshow`, `clock`.

---

## Data store conventions

- All data store keys use dotted namespaces: `cpu.temperature`, `nvidia.percent`,
  `memory.used_gb`, `net.bytes_recv_rate`, `disk.percent`, `system.hostname`.
- External pushes via Unix socket or REST use the same key namespace.
- Values are stored as Python primitives (float, int, str). No nested dicts in the store.

---

## Dev workflow (agent: when editing code during active development)

When working interactively with a human (not a cloud-assigned issue), after completing
edits the agent **must** restart the relevant development process:

```bash
./dev.sh restart
```

Then confirm the process restarted cleanly by checking `./dev.sh status`.

## Agent visual self-check workflow

When modifying templates or renderer behavior, the agent should self-validate
visual output by fetching the live rendered frame and inspecting it directly:

```bash
curl -sS http://localhost:18080/image -o /tmp/casedd-frame.jpg
```

Then review `/tmp/casedd-frame.jpg` with the available image-viewing tool before
finalizing layout/typography changes.

> **Note:** The `/image` endpoint serves **JPEG** (not PNG). The dev server runs on port
> **18080** (not 8080 which is reserved for production). Always use `http://localhost:18080`
> during development.

---

## Environment variables

All settings have sane defaults. See `.env.example` for the full list.

Key vars:
- `CASEDD_LOG_LEVEL` — DEBUG / INFO / WARNING / ERROR (default: INFO)
- `CASEDD_NO_FB=1` — disable framebuffer output (dev mode)
- `CASEDD_FB_DEVICE` — framebuffer device path (default: /dev/fb1)
- `CASEDD_WS_PORT` — WebSocket port (default: 8765)
- `CASEDD_HTTP_PORT` — HTTP viewer port (default: 8080)
- `CASEDD_SOCKET_PATH` — Unix socket path (default: /run/casedd/casedd.sock)
- `CASEDD_TEMPLATE` — active template name (default: system_stats)
- `CASEDD_REFRESH_RATE` — global refresh rate in Hz (default: 2.0)
- `CASEDD_CONFIG` — path to casedd.yaml (default: casedd.yaml)

---

## Linting and type checking

Run via `./dev.sh lint` or directly:

```bash
source .venv/bin/activate
ruff check .
mypy --strict casedd/
```

Both must pass with zero errors/warnings before any commit. The agent iterates on lint
errors automatically. If a rule cannot be satisfied without a human decision, stop and
ask — do not suppress rules with `# noqa` or `# type: ignore` without a comment
explaining exactly why it is necessary and safe.
