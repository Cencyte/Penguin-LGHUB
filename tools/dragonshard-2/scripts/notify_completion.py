#!/usr/bin/env python3
"""Notify the desktop when every selected binary has a verified indexed export."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from dragonshard import atomic_json


def load(path):
    return json.loads(path.read_text())


def completion_summary(programs, expected):
    identities = [p['sha256'] for p in programs]
    if not expected or len(identities) != len(set(identities)) or set(identities) != set(expected):
        raise ValueError('Queue does not cover the selected binary identities exactly')
    if any(p.get('state') != 'complete' for p in programs):
        return None
    counts = Counter()
    for program in programs:
        sha, coverage = program['sha256'], program.get('coverage', {})
        if coverage.get('sha256') != sha or not coverage.get('export_complete'):
            raise ValueError('Program marked complete without verified export coverage')
        path = Path(program['database']).resolve()
        # Read only; index creation already performed the full integrity/ordinal checks.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
            recorded = db.execute('SELECT count(*) FROM functions WHERE sha256=?', (sha,)).fetchone()[0]
            shards = [json.loads(row[0]) for row in db.execute('SELECT metadata FROM coverage WHERE sha256=?', (sha,))]
        if recorded != coverage['recognized_internal_functions'] or len(shards) != coverage['shards']:
            raise ValueError('Saved index count differs from verified coverage')
        outcomes = Counter({name: sum(s[name] for s in shards) for name in ('ok', 'failed', 'timeout')})
        if (sum(s['attempted'] for s in shards) != recorded or sum(outcomes.values()) != recorded or
                any(outcomes[name] != coverage['outcomes'].get(name, 0) for name in outcomes)):
            raise ValueError('Saved shard outcomes differ from verified coverage')
        counts.update(outcomes)
        counts['functions'] += recorded
    return dict(programs=len(programs), export_percent=100, **counts)


def notify(title, body, critical=False):
    command = ['notify-send', '--print-id', '--app-name=DragonShard-2', '--expire-time=0',
               '--hint=boolean:resident:true', '--icon=dialog-information',
               '--urgency=' + ('critical' if critical else 'normal'), title, body]
    return subprocess.check_output(command, text=True, timeout=15).strip()


def inspect(campaign):
    expected = {p['sha256'] for p in load(campaign / 'campaign.json')['programs']}
    pointer = load(campaign / 'depth-v2/swarm.json')
    root = Path(pointer['directory'])
    status = load(root / 'status.json')
    queue = load(root / 'queue.json')
    result = completion_summary(queue['programs'], expected)
    alerts = []
    for kind in ('swarm', 'parallel-analysis'):
        producer = load(campaign / 'depth-v2' / (kind + '.json'))
        state = load(Path(producer['directory']) / 'status.json')
        if state.get('state') == 'blocked_or_failed':
            alerts.append(kind + ': ' + str(state.get('error', state.get('reason', 'failed'))))
        elif state.get('state') != 'complete':
            active = subprocess.check_output(['systemctl', '--user', 'show', producer['unit'],
                '-p', 'ActiveState', '--value'], text=True, timeout=10).strip()
            if active not in ('active', 'activating'):
                alerts.append(kind + ' service stopped before completion')
    failed = [key for key, job in queue['jobs'].items() if job['state'] == 'failed']
    if failed:
        alerts.append(f'{len(failed)} export/analysis jobs failed; completed evidence is retained')
    handoff_pointer = campaign / 'depth-v2/storage-handoff.json'
    if handoff_pointer.exists():
        handoff = load(handoff_pointer)
        transfer = load(Path(handoff['state_file']))
        if transfer.get('state') == 'blocked_or_failed':
            alerts.append('Storage handoff: ' + str(transfer.get('error', 'failed')))
        elif transfer.get('state') != 'complete':
            active = subprocess.check_output(['systemctl', '--user', 'show', handoff['unit'],
                '-p', 'ActiveState', '--value'], text=True, timeout=10).strip()
            if active not in ('active', 'activating'):
                alerts.append('Storage handoff stopped before completion')
    guard = load(campaign.parent.parent / 'disk-guard.json')
    if time.time() - guard['at'] > 30:
        alerts.append('Disk protection monitor stopped reporting')
    elif guard.get('reasons'):
        alerts.extend(guard['reasons'])
    if result:
        result['gap_and_dataflow_complete'] = status['state'] == 'complete'
        result['catalog'] = str(root / 'catalog.json')
    return result, alerts, dict(indexed=status['programs_indexed'], total=len(expected),
                               pipeline_state=status['state'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('state', type=Path)
    parser.add_argument('--once', action='store_true', help='Read-only check; do not notify or persist')
    args = parser.parse_args()
    state = load(args.state) if args.state.exists() else dict(notified_alerts=[])
    if state.get('completion_notification_id'):
        return
    while True:
        try:
            result, alerts, progress = inspect(args.campaign)
        except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
            result, alerts, progress = None, ['Watcher could not verify campaign: ' + str(error)], {}
        if args.once:
            print(json.dumps(dict(result=result, alerts=alerts, progress=progress), indent=2))
            return
        state.update(at=time.time(), progress=progress, result=result, alerts=alerts)
        try:
            if result:
                body = (f"{result['programs']}/{result['programs']} binaries; {result['functions']:,} functions accounted for. "
                        f"C output: {result['ok']:,}; failures: {result['failed']:,}; timeouts: {result['timeout']:,}. "
                        f"Catalog: {result['catalog']}.")
                if not result['gap_and_dataflow_complete']:
                    body += ' Remaining gap/data-flow follow-up continues.'
                state['completion_notification_id'] = notify('DragonShard-2: 100% exported and indexed', body)
            else:
                for alert in alerts:
                    if alert not in state['notified_alerts']:
                        notify('DragonShard-2 needs attention', alert, critical=True)
                        state['notified_alerts'].append(alert)
            state.pop('notification_error', None)
        except (OSError, subprocess.SubprocessError) as error:
            state['notification_error'] = str(error)  # Retry when the desktop bus returns.
        atomic_json(args.state, state)
        if state.get('completion_notification_id'):
            return
        time.sleep(30)


if __name__ == '__main__':
    main()
