"""Check the exact published Git tree and all reachable commit identities.

This is a publication guard, not a guarantee that arbitrary text is anonymous.
Run a separate private identity check and review every file before publishing.
"""
import argparse
import re
import subprocess
import sys

ALIAS = '0gnm0-afk'
ALLOWED = {
    '.gitignore', 'README.md', 'LICENSE', 'ARCHITECTURE.md', 'PUBLICATION.md',
    'schema/규칙_스키마.md',
    'templates/매매일지.md', 'templates/방법론노트.md',
    'templates/일지_인터뷰질문표.md', 'templates/독서_발췌.md',
    'templates/독서_추출.md', 'templates/독서_검수대기.md',
    'examples/TEST-00_발췌.md', 'examples/TEST-00_추출.md',
    'examples/TEST-00_검수대기.md',
    'scripts/check_publication.py', '.github/workflows/public-privacy.yml',
}
PATTERNS = {
    'private key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'GitHub token': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b',
    'Google API key': r'\bAIza[0-9A-Za-z_-]{35}\b',
    'Telegram token': r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b',
    'AWS key': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'private home path': r'(?i)(?:[a-z]:[\\/]Users[\\/]|/(?:Users|home)/)[A-Za-z0-9_\uac00-\ud7a3][^\s/\\]*',
}

def check(repo, ref):
    def git(*args):
        return subprocess.check_output(['git', '-C', repo, *args], stderr=subprocess.DEVNULL)
    errors = []
    seen = set()
    for oid in git('rev-list', ref).decode().splitlines():
        commit = git('cat-file', 'commit', oid).decode('utf-8', errors='replace')
        headers = commit.split('\n\n', 1)[0]
        for role in ('author', 'committer'):
            match = re.search(r'^' + role + r' (.*?) <([^>]+)> ', headers, re.M)
            if not match or match[1] != ALIAS or not match[2].endswith('@users.noreply.github.com'):
                errors.append('commit identity must use the public alias and noreply')
        entries = git('ls-tree', '-rz', oid).decode().split('\0')
        paths = set()
        for entry in entries:
            if not entry:
                continue
            meta, path = entry.split('\t', 1)
            mode, kind, blob = meta.split()
            paths.add(path)
            if path not in ALLOWED:
                errors.append('file is not on the publication allowlist: ' + path)
            if mode != '100644' or kind != 'blob':
                errors.append('non-regular public file: ' + path)
                continue
            if (path, blob) in seen:
                continue
            seen.add((path, blob))
            data = git('cat-file', 'blob', blob)
            if len(data) > 250_000:
                errors.append('unexpected large public file: ' + path)
                continue
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                errors.append('non-text public file: ' + path)
                continue
            if path == 'LICENSE':
                holders = re.findall(r'^Copyright \(c\) \d{4} (.+)$', text, re.M)
                if [h.strip() for h in holders] != [ALIAS]:
                    errors.append('LICENSE must contain only the public copyright alias')
            for label, pattern in PATTERNS.items():
                if re.search(pattern, text):
                    errors.append(label + ' detected in ' + path)
            for address in re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', text):
                if not address.endswith('@users.noreply.github.com'):
                    errors.append('non-noreply email detected in ' + path)
        if 'LICENSE' not in paths:
            errors.append('LICENSE missing')
    return sorted(set(errors))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default='.')
    parser.add_argument('--ref', default='HEAD')
    args = parser.parse_args()
    try:
        errors = check(args.repo, args.ref)
    except (subprocess.CalledProcessError, ValueError):
        print('FAIL: could not inspect the complete Git tree/history')
        sys.exit(1)
    for error in errors:
        print('FAIL: ' + error)
    if not errors:
        print('PASS: publication allowlist, license, identities, and secret patterns')
    sys.exit(bool(errors))
