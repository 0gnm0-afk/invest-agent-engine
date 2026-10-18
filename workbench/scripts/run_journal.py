"""Local lifecycle receipts, with no request payloads, credentials or exception text."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import traceback
from uuid import uuid4
from atomic_io import atomic_write


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def error_frames(exc):
    return [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
            for f in traceback.extract_tb(exc.__traceback__)]


class RunJournal:
    def __init__(self, folder, kind, interval=15):
        self.path = Path(folder)/(kind+'-lifecycle-'+uuid4().hex+'.json')
        self.interval = interval
        self.record = dict(kind=kind, pid=os.getpid(), parent_pid=os.getppid(),
                           started_at=timestamp(), state='running', finished_at=None)
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def write(self):
        with self.lock:
            self.record['heartbeat_at'] = timestamp()
            try:
                atomic_write(self.path, json.dumps(self.record, ensure_ascii=True))
            except OSError as exc:
                # Diagnostic output must not stop the market worker.
                self.record['journal_write_error'] = type(exc).__name__

    def heartbeat(self):
        while not self.stop.wait(self.interval):
            self.write()

    def __enter__(self):
        self.write()
        self.thread = threading.Thread(target=self.heartbeat, daemon=True)
        self.thread.start()
        return self

    def complete(self, exit_code=0, **fields):
        with self.lock:
            self.record.update(fields, exit_code=exit_code,
                               state='succeeded' if exit_code == 0 else 'partial' if exit_code == 2 else 'failed')

    def __exit__(self, kind, exc, tb):
        self.stop.set()
        self.thread.join(timeout=3)
        if isinstance(exc, SystemExit):
            self.complete(exc.code if isinstance(exc.code, int) else 1 if exc.code else 0)
        elif exc is not None:
            self.record.update(state='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                               error_type=type(exc).__name__, error_frames=error_frames(exc))
        elif self.record['state'] == 'running':
            self.complete()
        self.record['finished_at'] = timestamp()
        self.write()
