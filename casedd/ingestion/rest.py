"""REST ingestion: documents the HTTP-based data update endpoint.

The ``POST /update`` REST endpoint is implemented in
:mod:`casedd.outputs.http_viewer` as part of the main FastAPI application.
This module re-exports the :class:`UpdateRequest` Pydantic model and documents
the expected request/response contract for external producers.

Endpoint summary
----------------
``POST /update`` (HTTP 204 No Content on success)

Request body (JSON)::

    {
        "update": {
            "cpu.temperature": 72.5,
            "memory.used_gb": 14.2,
            "disk.percent": 38
        }
    }

- Keys must be dotted namespace strings matching the data-store convention
  (e.g. ``cpu.temperature``, ``nvidia.percent``).
- Values must be ``float``, ``int``, or ``str``.
- Nested objects and ``null`` are rejected with HTTP 422.

Public API:
    - :class:`UpdateRequest` — re-exported Pydantic model for the update body
"""

from casedd.outputs.http_viewer import UpdateRequest

__all__ = ["UpdateRequest"]
