"""External-source connectors for the ContextQL server.

Each connector lives in its own subpackage and exposes provider classes
implementing the engine's MCP/REMOTE provider protocols. The first (and
currently only) connector is the mock DeepSee connector
(``app.connectors.deepsee``), which serves as the executable contract until
the DeepSee discovery gate is satisfied (plan section 8.5).
"""
