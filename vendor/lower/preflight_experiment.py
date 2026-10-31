#!/usr/bin/env python3
"""Read-only preflight before a new OHGRL/Chen experimental run."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from flow_registry import Registry, DEFAULT_DB


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--database',default=str(DEFAULT_DB))
    args=p.parse_args()
    database=Path(args.database).resolve()
    pending=[];error=None
    try:
        # mode=ro never creates a missing database, tables or a journal.
        with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
            pending=[Registry.decode(r) for r in db.execute("SELECT state,data FROM flows WHERE state != 'released' ORDER BY id")]
    except (sqlite3.Error,OSError,ValueError,KeyError,TypeError) as exc:
        error=str(exc)
    profile=Path(__file__).resolve().with_name('network_profile.json')
    result={
        'database':str(Path(args.database).resolve()),
        'pending_leases':len(pending),
        'pending':[ {k:r.get(k) for k in ('flow_id','state','dscp','tos','src_ip','dst_ip','src_port','dst_port','policy_version','controller_error')} for r in pending],
        'network_profile_sha256':hashlib.sha256(profile.read_bytes()).hexdigest(),
        'network_profile':json.loads(profile.read_text()),
        'safe_to_start_new_run':error is None and not pending,
        'error':error,
        'scope':'Registry-only check; does not prove switch/controller connectivity or absence of untracked rules.',
        'note':'Read-only. Never delete the SQLite DB to recycle TOS/DSCP; investigate and complete verified cleanup instead.'
    }
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if error is None and not pending else 2)
if __name__=='__main__':main()
