#!/usr/bin/env python3
"""Turn one vitest --reporter=json output into the `detail` payload
docker/scorer.sh writes into suite-<tag>.json.

Split out of scorer.sh (which used to inline this as a python heredoc) so
it can be unit-tested from the host with plain stdlib -- no docker, no
vitest, no network -- by feeding build_detail() a fake vitest JSON tree
directly. See scripts/tests/test_score_detail.py.

Usage (only ever invoked from inside the container by scorer.sh):
    score_detail.py <tag> <0|1 detail-on> <in vitest.json> <out suite.json>
"""
import hashlib
import json
import os
import sys

DETAIL_VERSION = 1  # bump if build_detail()'s output shape changes; score.py
                     # doesn't pin to this today, but a future consumer might.
DIGEST_LEN = 16      # hex chars -- matches scripts/events.py's DIGEST_LEN;
                      # short enough to keep the file small, long enough that
                      # ~130 hidden tests won't collide.


def test_digest(file_basename, full_name):
    """Content-addressed stand-in for a held-out test's identity. Same
    inputs always digest the same, across runs and across harnesses, so
    two runs' pass sets can be compared by digest equality alone -- the
    question issue #16 was actually raised over -- without the digest
    itself revealing what the test asserts. Never invertible in practice
    (sha256 over test-name-shaped text), but treat it as data derived
    from the oracle nonetheless: it still encodes which/how-many tests
    exist, so it stays inside the gitignored result directory, same as
    the file-level counts it accompanies."""
    canon = f'{file_basename}\0{full_name}'.encode('utf-8')
    return hashlib.sha256(canon).hexdigest()[:DIGEST_LEN]


def build_detail(tag, vitest_json):
    """Per-file and per-test entries from a vitest --reporter=json tree.

    `tag` is 'visible' or 'hidden'. The visible suite runs against the
    seed's own public tests, so its per-test entries carry plaintext
    names. The held-out suite's test NAMES are themselves benchmark data
    (see scorer.sh) -- emitting them in the clear, even to a host mount
    that never leaves this machine, would defeat the whole point of
    shipping it encrypted. So hidden per-test entries carry a digest
    (test_digest()) instead of a name; file-level entries still carry the
    file's basename, since which FILES exist is not secret (the suite's
    module layout is discoverable from SPEC.md/the visible suite already)
    -- only which individual tests they contain and what they're called.
    """
    files, tests = [], []
    for file_result in vitest_json.get('testResults', []):
        base = os.path.basename(file_result.get('name') or '')
        assertions = file_result.get('assertionResults') or []
        passed = sum(1 for a in assertions if a.get('status') == 'passed')
        failed = sum(1 for a in assertions if a.get('status') == 'failed')
        files.append({'file': base, 'passed': passed, 'failed': failed,
                      'total': len(assertions)})
        for a in assertions:
            full_name = a.get('fullName') or a.get('title') or ''
            status = a.get('status')
            if tag == 'visible':
                tests.append({'file': base, 'name': full_name, 'status': status})
            else:
                tests.append({'digest': test_digest(base, full_name), 'status': status})
    return {'detailVersion': DETAIL_VERSION, 'files': files, 'tests': tests}


def main(argv):
    tag, detail_on, in_path, out_path = argv[1], argv[2] == '1', argv[3], argv[4]
    vitest_json = json.load(open(in_path))
    o = {'passed': vitest_json.get('numPassedTests', 0),
         'failed': vitest_json.get('numFailedTests', 0),
         'total': vitest_json.get('numTotalTests', 0),
         'files': len(vitest_json.get('testResults', [])), '__hung': False}
    if detail_on or tag == 'visible':
        o['detail'] = build_detail(tag, vitest_json)
    json.dump(o, open(out_path, 'w'))


if __name__ == '__main__':
    main(sys.argv)
