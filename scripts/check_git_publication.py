"""Inspect committed public files and complete reachable commit metadata.

Print only fixed error codes: filenames and commit messages can contain secrets.
Run from a separately trusted copy for pre-publication approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

from check_publication import PATTERNS

BASELINE = 'ca120ec133abc838daef216146fd9c03679eb278'
IDENTITY = '0gnm0-afk <221542955+0gnm0-afk@users.noreply.github.com>'


def inspect(repo, ref='HEAD'):
    def git(*args):
        return subprocess.check_output(
            ['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL)

    errors = set()
    seen = set()

    def scan(raw):
        try:
            value = unicodedata.normalize('NFKC', raw.decode('utf-8-sig'))
        except UnicodeError:
            errors.add('G01')
            return
        value = ''.join(c for c in value if unicodedata.category(c) != 'Cf')
        for rule, pattern in PATTERNS.items():
            if re.search(pattern, value):
                errors.add(rule)

    if git('rev-parse', '--is-shallow-repository').strip() != b'false':
        return ['G02']
    commits = git('rev-list', ref).decode().splitlines()
    if BASELINE not in commits:
        errors.add('G03')
    for oid in commits:
        raw = git('cat-file', 'commit', oid)
        scan(raw)  # Includes complete message, not only author headers.
        headers = raw.decode('utf-8').split('\n\n', 1)[0]
        for role in ('author', 'committer'):
            match = re.search(r'^' + role + r' (.*? <[^>]+>) ', headers, re.M)
            if not match or match[1] != IDENTITY:
                errors.add('G04')
        files = {}
        for entry in git('ls-tree', '-rz', oid).split(b'\0'):
            if not entry:
                continue
            meta, name = entry.split(b'\t', 1)
            mode, kind, blob = meta.split()
            scan(name)
            if mode != b'100644' or kind != b'blob':
                errors.add('G05')
                continue
            if int(git('cat-file', '-s', blob.decode())) > 1_000_000:
                errors.add('G06')
                continue
            data = git('cat-file', 'blob', blob.decode())
            files[name.decode('utf-8')] = data
            if blob not in seen:
                scan(data)
                seen.add(blob)
        # The reviewed first public commit predates the manifest format.
        if oid == BASELINE:
            continue
        manifest_raw = files.pop('publication-manifest.json', None)
        if manifest_raw is None:
            errors.add('G07')
            continue
        manifest = json.loads(manifest_raw)
        actual = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        if (set(manifest) != {'schema_version', 'files'}
                or manifest['schema_version'] != 1 or manifest['files'] != actual):
            errors.add('G08')
    return sorted(errors)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--ref', default='HEAD')
    args = parser.parse_args()
    try:
        errors = inspect(args.repo, args.ref)
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError):
        errors = ['G00']
    print('FAIL: ' + ','.join(errors) if errors else 'PASS: committed files and complete history')
    sys.exit(bool(errors))
