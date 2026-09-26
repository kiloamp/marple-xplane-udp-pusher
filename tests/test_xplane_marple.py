import os
import unittest
from unittest.mock import patch

from xplane_marple import MarpleTrinoClient, get_sdk_db


class ConnectionTests(unittest.TestCase):
    def test_sdk_uses_db_token_and_configured_url(self):
        env = {"MARPLE_API_TOKEN": "query-test-token", "MARPLE_DB_API_TOKEN": "db-test-token",
               "MARPLE_DB_API_URL": "https://db.example.com/api/v1"}
        with patch.dict(os.environ, env, clear=True), patch('xplane_marple.load_local_env'), patch('marple.DB') as db:
            self.assertIs(get_sdk_db(), db.return_value)
            db.assert_called_once_with("db-test-token", "https://db.example.com/api/v1")

    def test_trino_uses_colleague_workspace_and_returns_dataframe(self):
        env = {"MARPLE_API_TOKEN": "query-test-token", "MARPLE_USER": "test_user",
               "MARPLE_HOT_CATALOG": "test_hot", "MARPLE_COLD_CATALOG": "test_cold",
               "MARPLE_DATAPOOL": "flights"}
        with patch.dict(os.environ, env, clear=True), patch('xplane_marple.load_local_env'), patch('xplane_marple.connect') as connect:
            client = MarpleTrinoClient()
            self.assertEqual(client.config.cold_catalog, "test_cold")
            self.assertEqual(client.config.datapool, "flights")
            self.assertEqual(connect.call_args.kwargs['catalog'], "test_hot")
            cursor = connect.return_value.cursor.return_value
            cursor.description = [('signal',), ('n',)]
            cursor.fetchall.return_value = [(1, 10)]
            self.assertEqual(client.execute('SELECT 1').dataframe.to_dict('records'), [{'signal': 1, 'n': 10}])

    def test_invalid_catalog_rejected_before_connecting(self):
        with patch.dict(os.environ, {"MARPLE_COLD_CATALOG": "cold; DROP TABLE data"}, clear=True), patch('xplane_marple.load_local_env'), patch('xplane_marple.connect') as connect:
            with self.assertRaises(ValueError):
                MarpleTrinoClient()
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
