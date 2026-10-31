"""Shared, persistent per-flow DSCP leases for one Mininet machine.

No timer releases a lease. Only the controller's all-switch absence proof plus
client socket closure permits release. SQLite serializes concurrent clients.
"""
import json
import sqlite3
import time
import uuid
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / 'runtime/perflow.sqlite3'
COOKIE_BASE = 0x4300000000000000

class PoolExhausted(RuntimeError): pass

class Registry:
    def __init__(self, path=DEFAULT_DB):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS flows (id INTEGER PRIMARY KEY AUTOINCREMENT, flow_id TEXT UNIQUE NOT NULL, dscp INTEGER NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS leased_dscp ON flows(dscp) WHERE state != 'released'")
            db.execute('CREATE TABLE IF NOT EXISTS controller (id INTEGER PRIMARY KEY CHECK(id=1), token TEXT, heartbeat REAL)')

    def connection(self):
        # Connections are short-lived; never share a connection across threads.
        return ClosingConnection(self.path)

    def register(self, data, pool=range(1,64)):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            used = {r[0] for r in db.execute("SELECT dscp FROM flows WHERE state != 'released'")}
            free = next((n for n in pool if n not in used), None)
            if free is None: raise PoolExhausted('All DSCP leases in use or awaiting verified cleanup')
            if not isinstance(free,int) or not 1 <= free <= 63: raise ValueError('DSCP must be 1..63')
            row = dict(data, flow_id=uuid.uuid4().hex, dscp=free, ecn=0, tos=free << 2,
                       registered_at=time.time(), registered_mono=time.monotonic())
            cur = db.execute('INSERT INTO flows(flow_id,dscp,state,data) VALUES(?,?,?,?)',
                             (row['flow_id'],free,'requested',json.dumps(row)))
            row['cookie'] = COOKIE_BASE + cur.lastrowid
            db.execute('UPDATE flows SET data=? WHERE id=?',(json.dumps(row),cur.lastrowid))
            return dict(row,state='requested')

    @staticmethod
    def decode(row):
        if row is None: return None
        return dict(json.loads(row[1]),state=row[0])

    def get(self, flow_id):
        with self.connection() as db:
            return self.decode(db.execute('SELECT state,data FROM flows WHERE flow_id=?',(flow_id,)).fetchone())

    def pending(self):
        with self.connection() as db:
            return [self.decode(r) for r in db.execute("SELECT state,data FROM flows WHERE state != 'released' ORDER BY id")]

    def latest_installed(self):
        with self.connection() as db:
            for row in db.execute("SELECT state,data FROM flows ORDER BY id DESC LIMIT 256"):
                item=self.decode(row)
                if (item.get('installed_at') and item.get('started_at') and item.get('flow_type')=='T'
                        and not item.get('controller_error') and item.get('app_outcome')!='failed'): return item
        return None

    def update(self, flow_id, state=None, allowed=None, **values):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self.decode(db.execute('SELECT state,data FROM flows WHERE flow_id=?',(flow_id,)).fetchone())
            if row is None: raise KeyError(flow_id)
            if allowed is not None and row['state'] not in allowed: return None
            old_state=row.pop('state');row.update(values)
            db.execute('UPDATE flows SET state=?,data=? WHERE flow_id=?',
                       (state or old_state,json.dumps(row),flow_id))
            return dict(row,state=state or old_state)

    def client_closed(self, flow_id, **result):
        return self.update(flow_id, client_closed_at=time.time(),client_closed_mono=time.monotonic(),**result)

    def release(self, flow_id, absent_dpids, removed_records, **values):
        row=self.get(flow_id)
        expected=set(row.get('switch_dpids',[]))
        if not row.get('client_closed_at') or expected != set(absent_dpids):
            raise ValueError('release needs closed client and absence confirmation from EVERY installed switch')
        if row['state'] not in ('deleting','quarantine'):raise ValueError('invalid release phase')
        now=time.time()
        return self.update(flow_id,state='released',released_at=now,
                           lifecycle_seconds=max(0,time.monotonic()-row['registered_mono']),
                           absence_confirmed_dpids=sorted(absent_dpids),
                           removed_records=removed_records,**values)

class ClosingConnection:
    def __init__(self,path):self.path=path
    def __enter__(self):
        self.db=sqlite3.connect(self.path,timeout=10)
        self.db.execute('PRAGMA busy_timeout=10000')
        return self.db
    def __exit__(self,kind,value,tb):
        try:
            if kind:self.db.rollback()
            else:self.db.commit()
        finally:self.db.close()
