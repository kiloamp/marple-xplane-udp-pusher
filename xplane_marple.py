"""Marple connections needed by the UDP recorder and cold-storage verification."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import pandas as pd
from trino.auth import BasicAuthentication
from trino.dbapi import connect

from env_loader import load_local_env


def get_sdk_db():
    load_local_env()
    token = os.getenv("MARPLE_DB_API_TOKEN") or os.getenv("MARPLE_API_TOKEN")
    if not token:
        raise ValueError("Set MARPLE_DB_API_TOKEN or MARPLE_API_TOKEN in .env.local.")
    from marple import DB
    api_url = os.getenv("MARPLE_DB_API_URL")
    return DB(token, api_url) if api_url else DB(token)


@dataclass(frozen=True)
class MarpleTrinoConfig:
    host: str = "query.db.marpledata.com"
    port: int = 443
    user: str = "mdb_demo_aerospace"
    hot_catalog: str = "mdb_demo_aerospace_hot"
    cold_catalog: str = "mdb_demo_aerospace_cold"
    datapool: str = "default"
    http_scheme: str = "https"


@dataclass(frozen=True)
class QueryResult:
    dataframe: pd.DataFrame


class MarpleTrinoClient:
    """Small Trino client for validating the recorder's cold-storage counts."""

    def __init__(self, config: MarpleTrinoConfig | None = None, token: str | None = None):
        load_local_env()
        self.config = config or MarpleTrinoConfig(
            host=os.getenv("MARPLE_TRINO_HOST", "query.db.marpledata.com"),
            port=int(os.getenv("MARPLE_TRINO_PORT", "443")),
            user=os.getenv("MARPLE_USER", "mdb_demo_aerospace"),
            hot_catalog=os.getenv("MARPLE_HOT_CATALOG", "mdb_demo_aerospace_hot"),
            cold_catalog=os.getenv("MARPLE_COLD_CATALOG", "mdb_demo_aerospace_cold"),
            datapool=os.getenv("MARPLE_DATAPOOL", "default"),
            http_scheme=os.getenv("MARPLE_HTTP_SCHEME", "https"),
        )
        for value in (self.config.hot_catalog, self.config.cold_catalog, self.config.datapool):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise ValueError("Marple catalog/datapool must be a SQL identifier.")
        token = token or os.getenv("MARPLE_API_TOKEN")
        if not token:
            raise ValueError("Set MARPLE_API_TOKEN for cold-storage verification.")
        self.connection = connect(
            host=self.config.host, port=self.config.port, user=self.config.user,
            auth=BasicAuthentication(self.config.user, token),
            catalog=self.config.hot_catalog, http_scheme=self.config.http_scheme,
        )

    def execute(self, sql: str) -> QueryResult:
        cursor = self.connection.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        columns = [column[0] for column in cursor.description or []]
        return QueryResult(dataframe=pd.DataFrame(rows, columns=columns))
