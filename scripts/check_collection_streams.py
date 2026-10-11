"""Regression checks for the isolated collection collector. No live requests."""
from contextlib import ExitStack
from datetime import date, timedelta
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'collectors/spotify/streams/tools/scripts/collection_streams.py'
spec = importlib.util.spec_from_file_location('collection_streams_check', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def check():
    # Small fixture counters live only inside this temporary test directory.
    today = date(2026, 10, 9).isoformat()
    previous = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    old = (date.fromisoformat(today) - timedelta(days=2)).isoformat()
    tid = 'fixture-track'
    remote = {'days': {old: {tid: {'s': 80, 'd': None}}, previous: {tid: {'s': 100, 'd': 20}}}}
    uploaded = []
    s3 = SimpleNamespace(
        get_object=lambda **kwargs: {'Body': io.BytesIO(json.dumps(remote).encode())},
        put_object=lambda **kwargs: uploaded.append(json.loads(kwargs['Body'])),
    )
    cfg = {'boto3': SimpleNamespace(client=lambda *args, **kwargs: s3),
           'account': 'fixture', 'key_id': 'fixture', 'secret': 'fixture', 'bucket': 'fixture'}
    imports = {
        'spotify_api': SimpleNamespace(fetch_playcount_api=lambda *args: 120),
        'stream_utils': SimpleNamespace(get_stats_date_str=lambda: today),
        'history_store': SimpleNamespace(_r2_config=lambda: cfg),
    }
    temporary_root = ROOT / 'runtime' / 'collection-checks'
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='check-', dir=temporary_root) as directory, ExitStack() as stack:
        root = Path(directory)
        assert root.resolve().is_relative_to(temporary_root.resolve())
        tracks = root / 'tracks.json'
        history = root / 'history.json'
        tracks.write_text(json.dumps([{'track_id': tid}]), encoding='utf-8')
        for name, value in [('ROOT', root), ('TRACKS', tracks), ('HISTORY', history)]:
            stack.enter_context(patch.object(module, name, value))
        stack.enter_context(patch.dict(sys.modules, imports))
        module.collect_songwriting(today, object())
        result = json.loads(history.read_text())
        assert result['days'][old] == remote['days'][old]
        assert result['days'][today][tid] == {'s': 120, 'd': 20}
        assert uploaded[-1] == result
        # A rerun must not replace a dated point with a later live counter.
        imports['spotify_api'].fetch_playcount_api = lambda *args: 140
        module.collect_songwriting(today, object())
        assert json.loads(history.read_text())['days'][today][tid] == {'s': 120, 'd': 20}
        remote['days'][today] = {tid: {'s': 130, 'd': 30}}
        try:
            module.collect_songwriting(today, object())
        except ValueError as exc:
            assert 'Conflicting' in str(exc)
        else:
            raise AssertionError('Conflicting local/R2 row accepted')
        try:
            module.collect_songwriting(previous, object())
        except ValueError:
            pass
        else:
            raise AssertionError('Historical live collection accepted')
    for total, before in [(None, {'s': 100}), (100, {'s': 100}), (90, {'s': 100}), (100, None)]:
        assert module.exact_point(total, before)['d'] is None
    print('Collector date guards, exact deltas, pending baselines, R2 preservation and conflicts: PASS')


if __name__ == '__main__':
    check()
