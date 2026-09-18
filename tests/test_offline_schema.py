"""Opening a supported local graph does not attempt extension downloads."""

from unittest.mock import Mock

import duckdb

from prme.storage import schema


def test_initialize_database_uses_builtin_sql_without_extension_installs(monkeypatch):
    install = Mock(side_effect=AssertionError("Unexpected extension install"))
    monkeypatch.setattr(schema, "install_duckpgq", install)
    with duckdb.connect(":memory:") as conn:
        assert schema.initialize_database(conn) is False
        assert schema.initialize_database(conn) is False
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        assert {"nodes", "edges", "events", "event_materializations"} <= tables
    install.assert_not_called()
