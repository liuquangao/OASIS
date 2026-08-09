from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from api.config import settings


@contextmanager
def db_connection():
    with psycopg.connect(settings.db_conninfo, row_factory=dict_row) as conn:
        yield conn
