"""Apply one operator-verified Dreama billing statement to an existing ledger."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from budget_policy import unique_json_object
from task_repository import RepositoryError, TaskRepository, own_task_ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path, help='Existing Dreama Bridge state directory')
    parser.add_argument('--evidence', required=True, type=Path, help='Operator-verified billing evidence JSON')
    args = parser.parse_args(argv)
    try:
        with args.evidence.open('rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise RepositoryError('Billing evidence file exceeds its limit')
        proof = json.loads(raw, object_pairs_hook=unique_json_object)
        database = args.state.resolve() / 'jobs.sqlite3'
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('SELECT version FROM p1_schema').fetchall() != [(2,)]:
                raise RepositoryError('An existing P6 ledger is required')
        with own_task_ledger(database.parent, exclusive=True):
            result = TaskRepository(database).apply_billing_evidence(proof)
    except (OSError, ValueError, sqlite3.Error):
        parser.exit(1, 'Billing evidence could not be applied to the original Dreama ledger.\n')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
