"""Read-only release/rollback gate. Never imports a gateway or creates a ledger."""
import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

ACTIVE={'preparation_queued','preparing','submitting','queued','processing'}
UNRESOLVED={'prepared','download_failed','submission_unknown','unknown'}


def inspect_ledger(root):
    path=Path(root).resolve()/'jobs.sqlite3'
    if not path.is_file():
        return {'ledgerSchemaVersion':0,'minimumReaderVersion':1,'activeCounts':{},'unresolvedCounts':{},'safeToReplace':True,'newBindings':0}
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)
    try:
        version=connection.execute('PRAGMA user_version').fetchone()[0]
        rows=[json.loads(row[0]) for row in connection.execute('SELECT result FROM jobs')]
    finally: connection.close()
    def active(row):
        attempted=bool(row.get('autoAttemptedQuoteId')) and row.get('autoAttemptedQuoteId')==row.get('quoteId')
        return (row.get('status') in ACTIVE or row.get('status')=='prepared' and bool(row.get('autoExecuteAt'))
                and not row.get('autoExecuteError') and not attempted)
    counts=Counter(row.get('status','unknown') for row in rows if active(row))
    unresolved=Counter(row.get('status','unknown') for row in rows if not active(row) and row.get('status','unknown') in UNRESOLVED)
    new=sum(row.get('bindingSnapshot',{}).get('source')=='canvas' for row in rows)
    return {'ledgerSchemaVersion':version,'minimumReaderVersion':2 if version>=2 or new else 1,
            'activeCounts':dict(counts),'unresolvedCounts':dict(unresolved),'safeToReplace':not counts and version<=2,'newBindings':new}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--state',required=True);args=parser.parse_args()
    print(json.dumps(inspect_ledger(args.state),ensure_ascii=False))
