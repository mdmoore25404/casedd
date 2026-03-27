"""Ingestion sub-package: receives data from external producers.

External producers can push key/value updates into the CASEDD data store
via two channels:

- :mod:`casedd.ingestion.unix_socket` — JSON messages over a Unix domain socket.
- :mod:`casedd.ingestion.rest` — ``POST /update`` HTTP endpoint (see
  :mod:`casedd.outputs.http_viewer`, which hosts the same endpoint).

The Unix socket listener is the preferred low-overhead path for local producers
(scripts running on the same host).  The REST endpoint is more convenient for
remote producers or anything that prefers HTTP.
"""
