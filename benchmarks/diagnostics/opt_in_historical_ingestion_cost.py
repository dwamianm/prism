"""Authenticate the shared historical ingestion cost without recounting it per arm."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

from benchmarks.integrations import run_longmemeval_s_baseline as baseline
from benchmarks.diagnostics.register_opt_in_interactions import file_sha, sha, write_new


def main():
    root = Path(__file__).resolve().parents[2]
    reports = root / 'benchmarks/results/research/2026-09-22'
    registration = json.loads((reports / 'opt-in-successor-v2-registration.json').read_text())
    master = Path('/Users/dwamianm/Sites/prism/data/benchmarks/longmemeval-s-prme-baseline-v1')
    identities = json.loads((master / 'identity.json').read_text())
    ordered = registration['longmemeval_s']['ordered_question_ids']
    rows = []
    for qid in ordered:
        path = master / 'captures' / f'{qid}.json'
        capture = json.loads(path.read_text())
        payload = {k: v for k, v in capture.items() if k != 'capture_sha256'}
        checksum = hashlib.sha256(baseline._canonical(payload)).hexdigest()
        if checksum != capture['capture_sha256'] or not capture['complete'] or capture['question_id'] != qid:
            raise ValueError('Historical capture authentication failed')
        rows.append({'question_id': qid, 'capture_sha256': checksum,
                     'capture_file_sha256': file_sha(path),
                     'ingestion_seconds': capture['timing']['ingestion_seconds'],
                     'stored_turns': capture['history']['stored_turns'],
                     'empty_turns_omitted': capture['history']['empty_turns_omitted']})
    seconds = [r['ingestion_seconds'] for r in rows]
    result = {'kind': 'shared-historical-ingestion-cost-ledger',
              'created_at': datetime.now(timezone.utc).isoformat(),
              'registration_sha256': registration['registration_sha256'],
              'master_identity_sha256': file_sha(master / 'identity.json'),
              'historical_registration_sha256': identities['registration_sha256'],
              'complete': len(rows) == 500, 'questions': len(rows),
              'stored_turns': sum(r['stored_turns'] for r in rows),
              'empty_turns_omitted': sum(r['empty_turns_omitted'] for r in rows),
              'sum_question_wall_seconds': sum(seconds),
              'median_question_seconds': statistics.median(seconds),
              'p95_question_seconds': baseline._percentile(seconds, .95),
              'new_ingestion_seconds_for_historical_retrieval_arms': 0,
              'new_ingestion_model_calls_for_historical_retrieval_arms': 0,
              'monetary_cost': None,
              'accounting': 'One previously paid direct-turn ingestion cost shared by all historical retrieval arms. The sum of per-question wall times is not total elapsed study time or CPU time. Cloning, integrity checks, retrieval, neural inference and reader/judge calls are separate expenses. Fresh ingestion arms use their own actual measured costs and matched fresh control. No hosted-price estimate or isolated-serving comparison is inferred.',
              'rows': rows}
    result['ledger_sha256'] = sha(result)
    write_new(reports / 'opt-in-shared-historical-ingestion-cost-v1.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
