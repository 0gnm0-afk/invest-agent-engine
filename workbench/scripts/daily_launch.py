"""One market cycle per KST launch day, independent of dashboard server ports."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo
from uuid import uuid4

from invest_agent.trading.market_provider import completed_sessions
from atomic_io import atomic_write
from run_journal import error_frames
from invest_agent.trading.store import instance_lock, BusyError


def utc_now():
    return datetime.now(timezone.utc)


def capped_sessions(sessions, cutoff=None):
    if cutoff is None:
        return sessions
    datetime.strptime(cutoff, '%Y-%m-%d')
    selected = [d for d in sessions if d <= cutoff]
    if not selected:
        raise ValueError('completed_sessions_unavailable')
    return selected


def plan_for(now):
    if now.tzinfo is None:
        raise ValueError('timezone_required')
    day = now.astimezone(ZoneInfo('Asia/Seoul')).date()
    cutoff = (day - timedelta(days=1)).isoformat()
    return {'day':day.isoformat(), 'cutoff':cutoff, 'evaluated_at':now.isoformat(),
            'targets':{m:capped_sessions(completed_sessions(m, now), cutoff)[-1] for m in ('KR','US')}}


def attempt_id(record):
    return record.get('attempt_id') or record.get('started_at') or record.get('day', '')

