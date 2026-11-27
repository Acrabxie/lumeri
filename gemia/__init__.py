"""The engine's name before the rename, kept for one release.

``python -m gemia`` runs ``python -m lumeri`` (see ``gemia/__main__.py``), so
a launch script or MCP client config written before the rename still starts
Lumeri. The next release drops this package; change ``-m gemia`` to
``-m lumeri`` before then.
"""
