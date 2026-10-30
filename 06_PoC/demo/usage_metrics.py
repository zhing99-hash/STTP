"""Opt-in local aggregates only; not a visitor counter or production analytics."""
from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3

STORE = Path(__file__).resolve().parent / 'db_work' / 'usage-metrics.sqlite'
VERSIONS = {'explore', 'engineering'}
EVENTS = {'enter', 'interface_click', 'search', 'node_open', 'switch_to_engineering',
          'switch_to_explore', 'select_explore', 'visible_seconds', 'graph_generate', 'feedback_open', 'export'}


def record(payload):
    if not isinstance(payload, dict) or set(payload) - {'version','event','seconds'}:
        raise ValueError('invalid event')
    version, event = payload.get('version'), payload.get('event')
    seconds = payload.get('seconds', 0)
    if not isinstance(version, str) or not isinstance(event, str) or version not in VERSIONS or event not in EVENTS or type(seconds) is not int:
        raise ValueError('invalid event')
    if (event == 'visible_seconds' and not 1 <= seconds <= 30) or (event != 'visible_seconds' and seconds != 0):
        raise ValueError('invalid seconds')
    STORE.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(STORE, timeout=5)) as con, con:
        con.execute('CREATE TABLE IF NOT EXISTS totals (day TEXT, version TEXT, event TEXT, count INTEGER NOT NULL, seconds INTEGER NOT NULL, PRIMARY KEY(day,version,event))')
        con.execute('INSERT INTO totals VALUES (?,?,?,?,?) ON CONFLICT(day,version,event) DO UPDATE SET count=count+1, seconds=seconds+excluded.seconds',
                    (date.today().isoformat(),version,event,1,seconds))


def summary():
    if not STORE.is_file(): return []
    with closing(sqlite3.connect(f'{STORE.as_uri()}?mode=ro', uri=True)) as con:
        try:
            rows=con.execute('SELECT version,event,SUM(count),SUM(seconds) FROM totals GROUP BY version,event ORDER BY version,event').fetchall()
        except sqlite3.OperationalError as error:
            if 'no such table' in str(error): return []
            raise
    return [{'date':'all','version':version,'event':event,'count':count,'seconds':seconds} for version,event,count,seconds in rows]
