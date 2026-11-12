#!/usr/bin/env python3
"""Verify files and provenance, then report whether the unique decision goal passed."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def summarize(out, goal):
    rows = {}
    errors = 0
    invalid = 0
    manifest = out / 'captures.jsonl'
    for line in manifest.read_text().splitlines() if manifest.exists() else []:
        try:
            r = json.loads(line)
            if r.get('status') != 'ok':
                errors += 1
                continue
            obj = (out / r['object']).resolve()
            if not obj.is_relative_to(out.resolve()):
                raise ValueError('Object outside collection')
            body = obj.read_bytes()
            if hashlib.sha256(body).hexdigest() != r['sha256']:
                raise ValueError('Hash mismatch')
            from canlii_crawler import valid_decision
            if not valid_decision(body.decode('utf-8')):
                raise ValueError('Not a decision')
            rows[r['url']] = {k: r.get(k, '') for k in ('url', 'title', 'at', 'sha256', 'object')}
        except (ValueError, KeyError, OSError, UnicodeError):
            invalid += 1
    with (out / 'decisions.csv').open('w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['url', 'title', 'at', 'sha256', 'object'])
        w.writeheader()
        w.writerows(rows.values())
    unique_hashes = len({r['sha256'] for r in rows.values()})
    result = {'goal': goal, 'verified_unique_urls': len(rows), 'unique_sha256': unique_hashes,
              'error_records': errors, 'invalid_records': invalid,
              'goal_reached': len(rows) >= goal and unique_hashes >= goal and invalid == 0}
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['goal_reached'] else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, default=Path('canlii_data'))
    p.add_argument('--goal', type=int, default=50)
    args = p.parse_args()
    raise SystemExit(summarize(args.out, args.goal))
