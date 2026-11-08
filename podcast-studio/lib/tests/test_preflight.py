"""No-network preflight regressions; ledgers and source files are synthetic."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib.openai_runtime import Budget, BudgetError
from lib.preflight import inspect_readiness

ROOT = Path(__file__).resolve().parents[2]
AT = '2026-11-07T05:00:00+08:00'


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        files = []
        for kind, published in [('news', '2026-11-06'), ('archive', '2026-03-01')]:
            p = self.base / (kind + '.md')
            p.write_text('Synthetic original source; limitations preserved.')
            files.append({'path': str(p), 'kind': kind, 'published': published,
                          'title': kind, 'url': 'https://example.org/' + kind,
                          'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
        self.manifest = self.base / 'manifest.json'
        self.data = {'version': 1, 'files': files, 'reviewed_at': '2026-11-06T12:00:00+00:00',
                     'candidates': [{'domain': 'tech', 'topic_tag': 'evidence', 'source_path': files[0]['path']}],
                     'archive_context': {'source_path': files[1]['path'], 'question': 'Who checks?'}}
        self.manifest.write_text(json.dumps(self.data))
        self.ledger = self.base / 'synthetic-ledger.json'
        self.ledger.write_text(json.dumps({'date': '2026-11-07', 'timezone': 'Asia/Shanghai',
            'commitments_verified': True, 'opening_committed_usd': .4,
            'reservations': [{'id': 'unknown', 'reserved_usd': .3}, {'id': 'settled', 'reserved_usd': .1}]}))
        self.production = {'provider': 'openai', 'language': 'en', 'source_mode': 'public',
            'source_manifest': str(self.manifest), 'source_manifest_sha256': hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            'source_authorized': True, 'budget_timezone': 'Asia/Shanghai', 'budget_ledger': str(self.ledger),
            'per_call_limit_usd': .10, 'daily_limit_usd': 2}
        self.config = self.base / 'config.yaml'
        self.config.write_text('Synthetic config placeholder, read through patched parser.')

    def audit(self):
        raw = {'production': self.production, 'vault': {'output_dir': str(self.base / 'never-create')}}
        with patch('lib.preflight._read_yaml', return_value=raw), patch('lib.preflight._resolve_config_path', return_value=self.config), patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-fixture-only'}, clear=True), patch('lib.config.load_config', side_effect=AssertionError('write-capable config loader called')):
            return inspect_readiness(show='morning', run_at=AT, policy_timezone='Asia/Shanghai')

    def test_ready_audit_keeps_every_reservation_and_all_files_unchanged(self):
        before = {p: p.read_bytes() for p in self.base.iterdir()}
        result = self.audit()
        self.assertTrue(result['ready_for_run'], result)
        self.assertAlmostEqual(result['budget']['committed_usd'], .8)
        self.assertAlmostEqual(result['budget']['remaining_usd'], 1.2)
        self.assertEqual(result['paid_calls'], 0)
        self.assertEqual(result['reservations_created'], 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.base.iterdir()})

    def test_malformed_ledger_top_level_returns_a_structured_block_without_writes(self):
        for value in ([], None, 'not an accounting object'):
            self.ledger.write_text(json.dumps(value))
            before = self.ledger.read_bytes()
            result = self.audit()
            self.assertFalse(result['ready_for_run'])
            self.assertTrue(any(x['check'] == 'shared_budget' and 'JSON object' in x['detail'] for x in result['checks']))
            self.assertEqual(before, self.ledger.read_bytes())

    def test_missing_ledger_never_initializes_one(self):
        missing = self.base / 'missing-ledger.json'
        self.production['budget_ledger'] = str(missing)
        self.assertFalse(self.audit()['ready_for_run'])
        self.assertFalse(missing.exists())

    def test_day_rollover_and_unverified_coverage_fail_without_reset(self):
        original = json.loads(self.ledger.read_text())
        for key, value in [('date', '2026-11-06'), ('commitments_verified', False)]:
            with self.subTest(key=key):
                data = dict(original)
                data[key] = value
                self.ledger.write_text(json.dumps(data))
                before = self.ledger.read_bytes()
                self.assertFalse(self.audit()['ready_for_run'])
                self.assertEqual(before, self.ledger.read_bytes())

    def test_stale_or_future_review_and_changed_sources_block(self):
        for timestamp in [None, '2026-11-04T01:00:00+08:00', '2026-11-08T01:00:00+08:00']:
            self.data['reviewed_at'] = timestamp
            self.manifest.write_text(json.dumps(self.data))
            self.production['source_manifest_sha256'] = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
            self.assertFalse(self.audit()['ready_for_run'])
        self.data['reviewed_at'] = '2026-11-06T12:00:00+00:00'
        self.manifest.write_text(json.dumps(self.data))
        self.production['source_manifest_sha256'] = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        (self.base / 'news.md').write_text('Changed source')
        self.assertFalse(self.audit()['ready_for_run'])

    def test_old_archive_is_allowed_but_old_news_and_budget_overrides_are_not(self):
        self.assertTrue(self.audit()['ready_for_run'])
        self.data['files'][0]['published'] = '2026-10-01'
        self.manifest.write_text(json.dumps(self.data))
        self.production['source_manifest_sha256'] = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        self.assertFalse(self.audit()['ready_for_run'])
        self.production['daily_limit_usd'] = 3
        self.assertFalse(self.audit()['ready_for_run'])

    def test_missing_null_or_malformed_approved_pin_never_passes_integrity(self):
        for pin in (None, '', 'bad'):
            self.production['source_manifest_sha256'] = pin
            result = self.audit()
            self.assertFalse(result['ready_for_run'])
            self.assertFalse(any(x['check'] == 'source_integrity' and x['ok'] for x in result['checks']))

    def test_budget_timezone_mismatch_blocks_trial_even_with_matching_other_metadata(self):
        self.production['budget_timezone'] = 'Etc/UTC'
        data = json.loads(self.ledger.read_text())
        data['timezone'] = 'Etc/UTC'
        data['date'] = '2026-11-06'
        self.ledger.write_text(json.dumps(data))
        self.assertFalse(self.audit()['ready_for_run'])

    def test_shanghai_calendar_is_used_for_news_when_input_clock_is_utc(self):
        self.data['files'][0]['published'] = '2026-11-07'
        self.manifest.write_text(json.dumps(self.data))
        self.production['source_manifest_sha256'] = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        raw = {'production': self.production}
        with patch('lib.preflight._read_yaml', return_value=raw), patch('lib.preflight._resolve_config_path', return_value=self.config), patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-fixture-only'}, clear=True):
            result = inspect_readiness(show='morning', run_at='2026-11-06T21:00:00+00:00', policy_timezone='Asia/Shanghai')
        self.assertTrue(result['ready_for_run'], result)

    def test_real_cli_rejects_preflight_time_without_audit_before_production(self):
        env = {'PATH': os.defpath, 'HOME': str(self.base), 'PYTHONPATH': str(ROOT),
               'PYTHONDONTWRITEBYTECODE': '1', 'PODCAST_STUDIO_CONFIG': str(self.base / 'absent.yaml')}
        for options in [('--preflight-at', AT), ('--max-news-age-days', '3'), ('--preflight-timezone', 'Asia/Shanghai')]:
            result = subprocess.run([sys.executable, '-m', 'lib.runner', '--show', 'morning',
                *options], cwd=self.base, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertIn('require --preflight', result.stderr)
        self.assertFalse((self.base / 'absent.yaml').exists())


if __name__ == '__main__':
    unittest.main()
