"""``python3 -m helios.runtime`` — run the service.

The whole configuration comes from the environment, so this takes no
arguments. Result codes are deliberately distinct, because a container
restart policy and an operator reading logs both need to tell them apart:

``0``
    a clean stop — the feed finished, a configured bound was reached, or a
    shutdown signal was honoured. The sink was flushed and closed.
``1``
    the service started and then failed.
``2``
    the service refused to start: configuration was missing or invalid, a
    strategy handoff would not resolve, or the feed was inconsistent.
"""

from helios.runtime.service import serve

raise SystemExit(serve())
