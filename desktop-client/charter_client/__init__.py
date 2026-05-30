"""Project Charter desktop client package.

The desktop client is split into focused modules:

  - ``config``        — env loading, constants, path anchors, logger, SDK guards
  - ``protocol``      — SSE parsing + streaming-event normalisation
  - ``auth``          — JWT decode, credential build, ``_BridgeTokenCredential``
  - ``storage``       — projects store, transcripts, view cache, sandbox readers
  - ``notifications`` — native Windows toasts
  - ``poller``        — background auto-check scheduler (``AutoPoller``)
  - ``bridge``        — the JS-callable ``Bridge`` (both transports)
  - ``tray``          — single-instance lock + system tray helpers

The entry point remains ``desktop-client/app.py``.
"""
