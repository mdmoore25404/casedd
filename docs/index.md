---
title: CASEDD Documentation
---

# CASEDD Documentation

This repository includes a live documentation site designed for GitHub Pages with source set to `docs/`.

- README content is available from `docs/README.md` (synced from `/README.md`).
- User documentation is in `docs/getters.md` and `docs/template_format.md`.

## README content in docs

`docs/README.md` is a copy of the project README and provides full project overview, quick start, configuration, and architecture information.

## User docs

- [Getter key reference](getters.md)
- [Template format](template_format.md)

## Setup in GitHub

1. Go to repository Settings > Pages.
2. Under "Source", set it to `docs/` folder on the `main` branch.
3. Save.

Once active, the site should be available at:

- `https://<your-org>.github.io/casedd`

## Local sync helper

To keep docs README in sync with root README:

```bash
./scripts/sync_docs_to_pages.sh
```

> This change closes issue #20.
