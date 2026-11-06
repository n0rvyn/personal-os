"""Real CLI smoke checks from a project cwd, with no host or provider credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys

import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / 'skills/podcast-studio-prep/scripts/orchestrator.py'


def offline_env(tmp_path):
    # Intentionally do not inherit provider keys or Claude-specific variables.
    home = tmp_path / 'home'
    home.mkdir(exist_ok=True)
    return {'HOME': str(home), 'PATH': os.defpath,
            'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1',
            'PODCAST_STUDIO_CONFIG': str(tmp_path / 'absent-config.yaml')}


def check_runner_help(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'lib.runner', '--help'],
        cwd=tmp_path, env=offline_env(tmp_path), capture_output=True, text=True,
        timeout=20, check=True)
    assert '--show {morning,evening,papers}' in result.stdout
    assert list(tmp_path.iterdir()) == [tmp_path / 'home']


def check_public_prep(tmp_path, show):
    vault = tmp_path / 'private'
    vault.mkdir()
    (vault / 'personal.md').write_text('PRIVATE-SENTINEL-NOT-A-SOURCE')
    archive = {'id': 'public-article', 'title': '原文标题',
               'excerpt': '原始文章没有被翻译或改写。',
               'url': 'https://example.org/original', 'published': '2026-11-05',
               'question': 'Who checks the evidence?'}
    args = [sys.executable, str(PREP), 'check', '--date', '2026-11-06',
            '--topic-log', str(tmp_path / 'synthetic-topic-log.yaml'),
            '--candidates', json.dumps([{'domain': 'tech', 'topic_tag': 'evidence'}]),
            '--show-type', show, '--vault-root', str(vault), '--seed', '1']
    public = subprocess.run([*args, '--source-mode', 'public', '--archive-context',
        json.dumps(archive)], cwd=tmp_path, env=offline_env(tmp_path),
        capture_output=True, text=True, timeout=20, check=True)
    brief = json.loads(public.stdout)
    assert not brief.get('error')
    assert brief['source_mode'] == 'public' and brief['pkos_note'] is None
    assert brief['archive_context'] == archive
    assert all(brief.get(k, []) == [] for k in
               ['cross_domain_candidates', 'self_past_candidates', 'ief_candidates'])
    assert 'PRIVATE-SENTINEL' not in public.stdout
    # The same CLI keeps the legacy contract; host import does not relax privacy.
    legacy = subprocess.run(args, cwd=tmp_path, env=offline_env(tmp_path),
        capture_output=True, text=True, timeout=20, check=True)
    assert 'pkos_note required' in json.loads(legacy.stdout)['error']
    missing = subprocess.run([*args, '--source-mode', 'public'], cwd=tmp_path,
        env=offline_env(tmp_path), capture_output=True, text=True, timeout=20,
        check=True)
    assert 'archive context' in json.loads(missing.stdout)['error']
    assert (vault / 'personal.md').read_text() == 'PRIVATE-SENTINEL-NOT-A-SOURCE'


class HostPortabilityTests(unittest.TestCase):
    def test_runner_help_without_claude_or_provider_environment(self):
        with tempfile.TemporaryDirectory() as d:
            check_runner_help(Path(d))

    def test_public_and_legacy_morning_contracts(self):
        with tempfile.TemporaryDirectory() as d:
            check_public_prep(Path(d), 'morning')

    def test_actual_dispatch_receives_explicit_source_mode_with_fake_transport(self):
        import datetime as dt
        import hashlib
        from types import SimpleNamespace
        from lib.openai_runtime import OpenAIDispatch, TEXT_MODEL, SPEECH_MODEL
        for public in (False, True):
            with self.subTest(public=public), tempfile.TemporaryDirectory() as d:
                base = Path(d).resolve()
                for name in ('news', 'notes', 'out', 'scratch'):
                    (base / name).mkdir()
                files = []
                for kind, directory in [('news', 'news'), ('archive', 'notes')]:
                    path = base / directory / 'source.md'
                    path.write_text('合成原文，不是真实私人资料。')
                    files.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                  'kind': kind, 'title': kind, 'url': 'https://example.org/' + kind,
                                  'published': '2026-11-05'})
                manifest = base / 'manifest.json'
                manifest.write_text(json.dumps({'version': 1, 'files': files,
                    'candidates': [{'domain': 'tech', 'topic_tag': 'evidence', 'source_path': files[0]['path']}],
                    'archive_context': {'source_path': files[1]['path'], 'question': 'Who checks?'}}))
                ledger = base / 'synthetic-ledger.json'
                ledger.write_text(json.dumps({'date': dt.datetime.now(dt.timezone.utc).date().isoformat(),
                    'timezone': 'Etc/UTC', 'commitments_verified': True,
                    'opening_committed_usd': 0, 'reservations': []}))
                settings = {'model': TEXT_MODEL, 'speech_model': SPEECH_MODEL, 'voice': 'alloy',
                    'show_title': 'Synthetic fixture', 'budget_timezone': 'Etc/UTC',
                    'budget_ledger': str(ledger), 'per_call_limit_usd': .1, 'daily_limit_usd': 2}
                if public:
                    settings.update(source_mode='public', source_manifest=str(manifest),
                        source_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
                config = SimpleNamespace(vault=SimpleNamespace(subjective_dir=str(base / 'notes'),
                    news_dir=str(base / 'news'), output_dir=str(base / 'out'), root=None))
                captured = []
                def fake_transport(endpoint, payload, **kwargs):
                    captured.append(json.dumps(payload))
                    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': 'Synthetic fixture artifact.'}]}]}
                adapter = OpenAIDispatch(settings, config, authorize_sources=True, transport=fake_transport)
                result = adapter('davinci', 'Synthetic offline instruction.', base / 'scratch',
                                 'material-summary.md', plugin_root=ROOT, step_name='material-test')
                self.assertTrue(result['ok'], result)
                self.assertEqual(len(captured), 1)
                self.assertIn('Source mode: public.' if public else 'Source mode: legacy.', captured[0])
                if public:
                    self.assertIn('pkos_note is null', captured[0])
                    self.assertIn('no WebSearch tool is available', captured[0])

    def test_public_and_legacy_evening_contracts(self):
        with tempfile.TemporaryDirectory() as d:
            check_public_prep(Path(d), 'evening')


if __name__ == '__main__':
    unittest.main()
