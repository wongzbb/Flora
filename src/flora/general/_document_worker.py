# SPDX-License-Identifier: Apache-2.0
"""Bounded parser subprocess protocol; not a public command."""

from __future__ import annotations

import json
import sys


def main():
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from flora.support.errors import ValidationError

    from .documents import MAX_DOCUMENT, _extract_document, _read_tables

    try:
        header = sys.stdin.buffer.readline(4096)
        request = json.loads(header)
        raw = sys.stdin.buffer.read(MAX_DOCUMENT + 1)
        if len(raw) > MAX_DOCUMENT or request.get("operation") not in {"extract", "tables"}:
            raise ValidationError("Invalid document parser request")
        function = _extract_document if request["operation"] == "extract" else _read_tables
        result = {"value": function(raw, request["suffix"])}
        text = json.dumps(result, ensure_ascii=False, allow_nan=False, default=str)
        if len(text.encode()) > 10 * 1024 * 1024:
            raise ValidationError("Parsed document result exceeds 10 MiB")
    except Exception as exc:
        text = json.dumps(
            {
                "error": str(exc)
                if isinstance(exc, ValidationError)
                else "Document parsing failed: " + type(exc).__name__
            }
        )
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
