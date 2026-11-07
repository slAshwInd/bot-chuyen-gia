"""Fail-closed source synchronization/Git check. Never traverses secrets.
Run from the source-only repository: python source_guard.py
It checks paths from the index and staged NEW blobs, not .gitignore alone.
No deployment, Git add, commit or push is performed.
"""
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
DENIED={'secrets','state','logs','data','charts','.venv','__pycache__','updates','upcode'}
PATTERNS=[re.compile(rb'\b[0-9]{6,12}:[A-Za-z0-9_-]{30,}\b'),
          re.compile(rb'gh[pousr]_[A-Za-z0-9_]{20,}'),
          re.compile(rb'github_pat_[A-Za-z0-9_]{20,}'),
          re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
          re.compile(rb'(?i)(?:api[_-]?(?:key|secret)|password|access[_-]?token)\s*[=:]\s*[\x22\x27][A-Za-z0-9_+/=-]{20,}[\x22\x27]')]

def git(*args):
    return subprocess.run(['git','-C',str(ROOT),*args],check=True,capture_output=True).stdout

def main():
    allowed=set((ROOT/'SOURCE_ALLOWLIST.txt').read_text(encoding='utf-8-sig').splitlines())
    failures=[]
    for name in allowed:
        pp=PurePosixPath(name)
        if not name or pp.is_absolute() or '..' in pp.parts or any(c.lower() in DENIED for c in pp.parts):
            failures.append('unsafe allowlist path: '+name)
    tracked=git('ls-files','-z').decode().split('\0')
    for name in filter(None,tracked):
        pp=PurePosixPath(name)
        if name not in allowed or any(c.lower() in DENIED for c in pp.parts):
            failures.append('tracked path not permitted: '+name)
            continue  # NEVER read disallowed paths or credentials.
        f=ROOT/name
        if f.is_symlink() or not f.resolve().is_relative_to(ROOT):
            failures.append('symlink/path escape: '+name)
            continue
        data=git('show',':'+name)
        if any(rx.search(data) for rx in PATTERNS):
            failures.append('possible credential in staged source: '+name)
        if f.is_file() and any(rx.search(f.read_bytes()) for rx in PATTERNS):
            failures.append('possible credential in working source: '+name)
    # Also inspect the full new tree and each new commit, not just final diff.
    try:
        commits=git('rev-list','origin/main..HEAD').decode().splitlines()
    except subprocess.CalledProcessError:
        failures.append('missing origin/main; cannot validate outgoing range')
        commits=[]
    for commit in commits:
        names=git('ls-tree','-r','--name-only',commit).decode().splitlines()
        for name in names:
            if name not in allowed:
                failures.append('unapproved path in outgoing commit '+commit[:12]+': '+name)
                continue
            blob=git('show',commit+':'+name)
            if any(rx.search(blob) for rx in PATTERNS):
                failures.append('possible credential in outgoing commit '+commit[:12]+': '+name)
    if failures:
        print('\n'.join(failures))
        return 1
    print('PASS: tracked paths, staged blobs, working source and outgoing commits checked against allowlist.')
    print('Pattern scan is not proof that no secret exists; manual diff review is still required.')
    return 0

if __name__=='__main__': sys.exit(main())
