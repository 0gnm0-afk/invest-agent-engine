"""Check a clean release directory against a separately reviewed manifest.

This does not inspect Git history or authenticate a modified checker/manifest.
Never print raw filenames, file contents, or exceptions in public logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import unicodedata

PATTERNS = {
    'P01': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'P02': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b',
    'P03': r'\bAIza[0-9A-Za-z_-]{35}\b',
    'P04': r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b',
    'P05': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'P06': r'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b',
    'P07': r'(?i)(?:[a-z]:[\\/]Users[\\/]|/(?:Users|home)/)[A-Za-z0-9_\uac00-\ud7a3]',
    'P08': r'[\w.+-]+@(?!users\.noreply\.github\.com)[\w.-]+\.[A-Za-z]{2,}',
}


def inspect(root):
    root=Path(root).resolve()
    errors=set()
    manifest_path=root/'publication-manifest.json'
    if manifest_path.is_symlink():
        return ['M01']
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    expected=manifest['files']
    if manifest.get('schema_version') != 1 or not isinstance(expected,dict) or not expected:
        return ['M02']
    # The manifest cannot hash itself. Its digest belongs in private release evidence.
    actual={}
    paths=[]
    for parent, directories, files in os.walk(root, followlinks=False):
        for directory in directories[:]:
            path=Path(parent)/directory
            if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
                errors.add('F01')
                directories.remove(directory)
        paths.extend(Path(parent)/name for name in files)
    for path in paths:
        if path.is_symlink() or getattr(path, 'is_junction', lambda:False)():
            errors.add('F01')
            continue
        if not path.is_file():
            continue
        rel=path.relative_to(root).as_posix()
        if rel == 'publication-manifest.json':
            continue
        if path.stat().st_size > 1_000_000:
            errors.add('F02')
            continue
        data=path.read_bytes()
        actual[rel]=hashlib.sha256(data).hexdigest()
        try:
            value=unicodedata.normalize('NFKC',rel+'\n'+data.decode('utf-8-sig'))
        except UnicodeError:
            errors.add('F03')
            continue
        value=''.join(c for c in value if unicodedata.category(c)!='Cf')
        for rule,pattern in PATTERNS.items():
            if re.search(pattern,value):
                errors.add(rule)
    if set(actual) != set(expected):
        errors.add('M03')
    if any(actual.get(name) != digest for name,digest in expected.items()):
        errors.add('M04')
    # Inspect metadata too; arbitrary new manifest keys are not allowed.
    if set(manifest) != {'schema_version','files'}:
        errors.add('M05')
    for rule,pattern in PATTERNS.items():
        if re.search(pattern, json.dumps(manifest,ensure_ascii=False)):
            errors.add(rule)
    return sorted(errors)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='.')
    args=parser.parse_args()
    try:
        errors=inspect(args.root)
    except (OSError,ValueError,TypeError,KeyError):
        errors=['M00']
    print('FAIL: '+','.join(errors) if errors else 'PASS: release inventory, hashes and secret patterns')
    sys.exit(bool(errors))
