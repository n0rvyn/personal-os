"""Network-free contracts for the opt-in English/OpenAI adapter."""
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pytest

from lib.dispatch import DispatchError
from lib.english import check_english
from lib.openai_runtime import Budget, OpenAIDispatch, OpenAIError, SPEECH_MODEL, TEXT_MODEL

ENGLISH = ('We have a choice in the way we build this system. The tools can help us, '
           'but the work is ours and the result will depend on what we do. ') * 90
ROOT = Path(__file__).resolve().parents[2]


def synthetic_meaning_review(scratch, text):
    """Unit-test-only verdict for the synthetic ENGLISH fixture, never production."""
    import hashlib
    quotes=[('fact','The tools can help us'), ('caveat','but the work is ours'),
            ('reasoning_or_opinion','the result will depend on what we do')]
    verdict={'english_listening_review':{'accessible_language':True,'meaning_preserved':True,'issues':[],
             'evidence':[{'category':kind,'reader_quote':quote,'spoken_quote':quote} for kind,quote in quotes]},
             'english_review_artifact_hashes':{'reader':hashlib.sha256(text.encode()).hexdigest(),
                                              'spoken':hashlib.sha256(text.encode()).hexdigest()}}
    (Path(scratch)/'scorecard-verdict.json').write_text(json.dumps(verdict))


def response(text):
    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': text}]}]}


@pytest.fixture
def setup(tmp_path):
    for folder in ['notes', 'news', 'out', 'scratch']:
        (tmp_path / folder).mkdir()
    ledger = tmp_path / 'ledger.json'
    ledger.write_text(json.dumps({'date': dt.datetime.now(dt.timezone.utc).date().isoformat(),
        'timezone': 'Etc/UTC', 'commitments_verified': True, 'opening_committed_usd': .5, 'reservations': []}))
    profile = {'model': TEXT_MODEL, 'speech_model': SPEECH_MODEL, 'voice': 'alloy', 'show_title': 'Configured English Show',
        'budget_timezone': 'Etc/UTC', 'per_call_limit_usd': .1, 'daily_limit_usd': 2, 'budget_ledger': str(ledger)}
    config = SimpleNamespace(vault=SimpleNamespace(subjective_dir=str(tmp_path / 'notes'),
        news_dir=str(tmp_path / 'news'), output_dir=str(tmp_path / 'out'), root=None))
    return tmp_path, profile, config


def adapter(setup, transport=lambda *a, **k: response('artifact')):
    _, profile, config = setup
    return OpenAIDispatch(profile, config, authorize_sources=True, transport=transport)


def invoke(a, setup, agent='davinci', artifact='material-summary.md', step='material-test'):
    return a(agent, 'Follow the station contract.', setup[0] / 'scratch', artifact,
             plugin_root=ROOT, step_name=step)


def test_language_contract():
    assert check_english(ENGLISH)['ok']
    assert not check_english('这是中文素材。' * 3000)['ok']
    assert not check_english('We have a short sentence.')['ok']
    assert not check_english('Esta es una historia sobre los sistemas y las personas. ' * 300)['ok']


def test_authorization_and_key_are_fail_closed(setup, monkeypatch):
    _, p, c = setup
    with pytest.raises(OpenAIError, match='authorization'):
        OpenAIDispatch(p, c)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(OpenAIError, match='absent'):
        OpenAIDispatch(p, c, authorize_sources=True)
    assert not json.loads(Path(p['budget_ledger']).read_text())['reservations']


def test_unpriced_models_rejected(setup):
    setup[1]['model'] = 'unknown'
    with pytest.raises(OpenAIError, match='Unpriced'):
        adapter(setup)


def test_actual_commitments_prevent_call(setup):
    setup[1]['daily_limit_usd'] = .5
    seen = []
    result = invoke(adapter(setup, lambda *a, **k: seen.append(a)), setup)
    assert not result['ok'] and not seen
    assert 'Daily cost limit' in result['reason']


@pytest.mark.parametrize('change', ['date', 'verified', 'nan', 'missing'])
def test_budget_rejects_invalid_state(setup, change):
    p = setup[1]
    path = Path(p['budget_ledger'])
    state = json.loads(path.read_text())
    if change == 'date':
        state['date'] = '2000-01-01'
    elif change == 'verified':
        state['commitments_verified'] = False
    elif change == 'nan':
        state['opening_committed_usd'] = float('nan')
    else:
        path.unlink()
    if change != 'missing':
        path.write_text(json.dumps(state))
    with pytest.raises((OpenAIError, FileNotFoundError)):
        Budget(p).reserve([.01], 'test')


def test_budget_lock_includes_parallel_commitments(setup):
    p = setup[1]
    p['daily_limit_usd'] = .6
    def reserve(_):
        try:
            Budget(p).reserve([.04], 'parallel')
            return True
        except OpenAIError:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        successes = sum(pool.map(reserve, range(8)))
    assert successes == 2
    assert len(json.loads(Path(p['budget_ledger']).read_text())['reservations']) == 2


def test_paths_and_symlinks_do_not_escape(setup):
    a = adapter(setup)
    root = setup[0]
    (root / 'secret.txt').write_text('outside source scope')
    (root / 'notes/link.txt').symlink_to(root / 'secret.txt')
    for target in ['../secret.txt', str(root / 'notes/link.txt')]:
        with pytest.raises(OpenAIError):
            a._local_tool('read_file', {'path': target, 'offset': 0}, root / 'scratch', ROOT, 'davinci')
    with pytest.raises(DispatchError):
        invoke(a, setup, artifact='../escape.md')
    with pytest.raises(DispatchError):
        invoke(a, setup, agent='arbitrary')


def test_original_source_language_preserved_tool_round_trip(setup):
    root = setup[0]
    note = root / 'notes/original.md'
    note.write_text('原文保持不变。')
    payloads = []
    def transport(endpoint, payload, **kwargs):
        payloads.append(json.loads(json.dumps(payload)))
        if len(payloads) == 1:
            return {'status': 'completed', 'output': [{'type': 'function_call', 'name': 'read_file',
                'arguments': json.dumps({'path': str(note), 'offset': 0}), 'call_id': 'read-1'}]}
        assert payload['input'][-1]['output'] == '原文保持不变。'
        return response('English material summary')
    result = invoke(adapter(setup, transport), setup)
    assert result['ok']
    assert note.read_text() == '原文保持不变。'
    assert (root / 'scratch/material-summary.md').read_text() == 'English material summary'
    assert payloads[0]['store'] is False and 'previous_response_id' not in payloads[0]
    assert 'ORIGINAL language' in payloads[0]['instructions']


def test_bible_isolation_and_structured_scoring(setup):
    payloads = []
    def transport(endpoint, payload, **kwargs):
        payloads.append(payload)
        return response('{}')
    a = adapter(setup, transport)
    assert invoke(a, setup, agent='bible-distiller', artifact='bible.md', step='bible-distill')['ok']
    assert payloads[0]['tools'] == []
    assert invoke(a, setup, agent='qianzhongshu', artifact='score.json', step='scoring')['ok']
    assert not any('prep_check' == t['name'] for t in payloads[1]['tools'])
    with pytest.raises(OpenAIError, match='isolation'):
        a._local_tool('read_file', {}, setup[0] / 'scratch', ROOT, 'bible-distiller')


def test_incomplete_and_non_english_do_not_land(setup):
    a = adapter(setup, lambda *a, **k: {'status': 'incomplete', 'output': []})
    assert not invoke(a, setup)['ok']
    a.transport = lambda *a, **k: response(json.dumps({'body': '中文文稿', 'title': 'Title'}))
    assert not invoke(a, setup, artifact='finalize-result.json', step='finalize')['ok']
    assert not (setup[0] / 'scratch/finalize-result.json').exists()


def test_uncertain_request_keeps_reservation_and_blocks_retry(setup):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('do not expose provider details')
    a = adapter(setup, fail)
    first = invoke(a, setup)
    second = invoke(a, setup)
    assert not first['ok'] and not second['ok'] and len(calls) == 1
    assert len(json.loads(Path(setup[1]['budget_ledger']).read_text())['reservations']) == 1
    assert (setup[0] / 'scratch/.openai-request-review.json').is_file()
    assert 'provider details' not in first['reason']


def test_resume_rejects_legacy_or_different_profile(setup):
    a = adapter(setup)
    scratch = setup[0] / 'scratch'
    (scratch / 'old.md').write_text('old')
    with pytest.raises(OpenAIError, match='unmarked'):
        a.bind_run(scratch, resume=True)
    (scratch / 'old.md').unlink()
    a.bind_run(scratch)
    a.bind_run(scratch, resume=True)
    a.settings['voice'] = 'nova'
    with pytest.raises(OpenAIError, match='differs'):
        a.bind_run(scratch, resume=True)


def test_existing_continuity_and_output_paths_are_reused(setup):
    old = setup[0] / 'out/2026-06-11-morning.stance.yaml'
    old.write_text('episode: old')
    a = adapter(setup)
    assert a.config.vault.output_dir == setup[2].vault.output_dir
    assert old.read_text() == 'episode: old'
    assert sorted(p.name for p in (setup[0] / 'out').iterdir()) == [old.name]


def test_prep_runs_real_fixed_command_without_shell(setup, monkeypatch):
    a = adapter(setup)
    calls = []
    def run(argv, **kw):
        calls.append((argv, kw))
        return SimpleNamespace(returncode=0, stdout='{"approved_topics": []}')
    monkeypatch.setattr(subprocess, 'run', run)
    args = {'candidates_json': '[]', 'pkos_note_json': '{}', 'date': '2026-11-05',
            'show': 'morning', 'domain': 'philosophy', 'contrarian': 'lesswrong'}
    assert json.loads(a._local_tool('prep_check', args, setup[0] / 'scratch', ROOT, 'davinci')) == {'approved_topics': []}
    argv, kwargs = calls[0]
    assert argv[2] == 'check' and '--topic-log' in argv and not kwargs.get('shell')
    assert str(setup[0] / 'out/topic_log.yaml') in argv
    args['domain'] = '$(arbitrary command)'
    with pytest.raises(OpenAIError):
        a._local_tool('prep_check', args, setup[0] / 'scratch', ROOT, 'davinci')


def test_speech_real_merge_and_recovery_without_paid_calls(setup):
    setup[1].update(episode_kind='demo', min_words=2600, target_words=3000, max_words=3900)
    setup[1]['daily_limit_usd'] = 10  # Synthetic fixture, not an approved live budget.
    root = setup[0]
    # Actual ffmpeg media decoding/merging; mocked provider audio is a synthetic
    # sine tone, NOT generated narration or a claim of live API validation.
    tone = root / 'tone.pcm'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.1', '-ar', '24000', '-ac', '1', '-f', 's16le', str(tone)], check=True)
    seen = []
    def speech(endpoint, payload, **kw):
        assert endpoint == 'realtime'
        assert payload['response']['max_output_tokens'] == 1536
        assert len(payload['response']['input'][0]['content'][0]['text'].split()) <= 80
        seen.append(payload)
        return {'pcm': tone.read_bytes(), 'transcript': payload['response']['input'][0]['content'][0]['text']}
    a = adapter(setup, speech)
    script = root / 'scratch/broadcast-script-2026-11-05.txt'
    script.write_text(ENGLISH)
    out = root / 'scratch/audio-files.mp3'
    a.synthesize(root / 'scratch', out)
    assert out.stat().st_size > 0 and len(seen) > 1
    assert script.read_text() == ENGLISH
    assert 'AI-generated voice' in (root / 'scratch/audio-disclosure.md').read_text()
    count = len(seen)
    out.unlink()
    a.synthesize(root / 'scratch', out)
    assert len(seen) == count  # reuse all successful chunks without repeat billing
    assert out.stat().st_size > 0


def test_whole_speech_job_is_budgeted_before_first_chunk(setup):
    setup[1]['daily_limit_usd'] = .51
    seen = []
    a = adapter(setup, lambda *args, **kw: seen.append(args))
    script = setup[0] / 'scratch/broadcast-script-2026-11-05.txt'
    script.write_text(ENGLISH)
    (setup[0] / 'scratch/finalize-result.json').write_text(json.dumps({'title':'Title','body':ENGLISH}))
    synthetic_meaning_review(setup[0] / 'scratch', ENGLISH)
    with pytest.raises(OpenAIError, match='Daily cost'):
        a.synthesize(setup[0] / 'scratch', setup[0] / 'scratch/audio-files.mp3')
    assert not seen


def test_collect_requires_actual_prep_routes_and_appends_host_briefs(setup, monkeypatch):
    calls = []
    routes = [('philosophy', 'lesswrong'), ('cognition', 'marginal-revolution'), ('history', 'stratechery')]
    def transport(endpoint, payload, **kwargs):
        calls.append(payload)
        if len(calls) == 1:
            return {'status': 'completed', 'output': [
                {'type': 'function_call', 'name': 'prep_check', 'call_id': str(i),
                 'arguments': json.dumps({'domain': domain, 'contrarian': source})}
                for i, (domain, source) in enumerate(routes)]}
        return response('## 当日新闻背景\n- **Example**: The original source is retained. (source: vault, 2026-11-05)')
    a = adapter(setup, transport)
    monkeypatch.setattr(a, '_local_tool', lambda name, args, *rest: json.dumps({
        'approved_topics': [{'topic_tag': args['domain']}]}))
    result = invoke(a, setup, step='collect')
    assert result['ok']
    content = (setup[0] / 'scratch/material-summary.md').read_text()
    for tag in 'ABC':
        assert f'brief-{tag}\n```json' in content
    assert 'English show/channel name: Configured English Show' in calls[0]['instructions']
    a.transport = lambda *args, **kw: response('I invented three briefs.')
    assert not invoke(a, setup, step='collect')['ok']


def test_bilingual_cards_share_history_without_overwriting(tmp_path):
    from lib.stance import write_card, load_cards, new_bet_id
    from lib.episode import stance_path
    today = dt.date.today().isoformat()
    zh = {'episode': {'date': today, 'show': 'morning'}, 'bets': [], 'open_questions': ['原有问题']}
    en = {'episode': {'date': today, 'show': 'morning', 'language': 'en'}, 'bets': [], 'open_questions': ['English question']}
    original = write_card(tmp_path, today, 'morning', zh)
    original_bytes = original.read_bytes()
    translated = write_card(tmp_path, today, 'morning', en)
    assert original != translated and translated.name == f'{today}-morning-en.stance.yaml'
    assert original.read_bytes() == original_bytes
    cards = load_cards(tmp_path)
    assert len(cards) == 2 and {q for c in cards for q in c['open_questions']} == {'原有问题', 'English question'}
    assert new_bet_id(today, 'morning', 1) != new_bet_id(today, 'morning-en', 1)
    with pytest.raises(FileExistsError):
        write_card(tmp_path, today, 'morning', en)


def test_english_duration_uses_words_without_changing_legacy_gate():
    from lib.scorecard import _english_duration, _hard_gate_duration
    assert _english_duration(ENGLISH)['ok']
    # Many short words pass the old Chinese character estimate but fail the
    # explicit English 18-minute floor; no false equivalence between units.
    short_english = ('We consider the consequences carefully. ' * 260)
    assert _hard_gate_duration(short_english)['ok']
    assert not _english_duration(short_english)['ok']


def test_english_publication_recovers_partial_outputs_without_overwrite(setup):
    from lib.runner import _publish_step, RunnerError
    scratch = setup[0] / 'scratch'
    episodes = setup[0] / 'out/episodes'
    episodes.mkdir()
    (scratch / 'finalize-result.json').write_text(json.dumps({'title': 'A real choice', 'body': ENGLISH}))
    ctx = {'scratch_dir': scratch, 'episodes_dir': str(episodes), 'output_dir': str(episodes.parent),
           'date': '2026-11-05', 'show': 'morning', 'no_tts': False, 'output_language': 'en'}
    with pytest.raises(RunnerError, match='audio is missing'):
        _publish_step(ctx)
    (scratch / 'audio-files.mp3').write_bytes(b'synthetic-media-test')
    (scratch / 'audio-disclosure.md').write_text('Voice disclosure: AI-generated voice, not a human narrator.')
    paths = _publish_step(ctx)
    assert paths['script'].read_text() == ENGLISH
    assert paths['audio'].read_bytes() == b'synthetic-media-test'
    _publish_step(ctx)  # completed output may be reused, never overwritten
    paths['script'].write_text('Existing independent manuscript')
    with pytest.raises(OpenAIError, match='no overwrite'):
        _publish_step(ctx)
    assert paths['script'].read_text() == 'Existing independent manuscript'


def test_run_pipeline_uses_existing_config_paths_and_checks_before_tts(tmp_path, monkeypatch):
    from lib.config import PodcastTeamConfig, VaultConfig, TtsConfig, PodcastIdentity, ProductionConfig
    from lib import runner, openai_runtime
    output = tmp_path / 'existing-output'
    output.mkdir()
    cfg = PodcastTeamConfig(vault=VaultConfig(subjective_dir=str(tmp_path), news_dir=str(tmp_path),
        output_dir=str(output), episodes_dir=str(output / 'episodes'),
        state_dir=str(output / 'state'), reports_dir=str(output / 'reports')),
        tts=TtsConfig(provider='volc', host_voice='original'),
        podcast=PodcastIdentity(name='Existing Chinese Show', english_name='Configured English Show'),
        production=ProductionConfig(provider='openai', language='en', options={'source_authorized': False}))
    bound, executed = [], []
    class FakeDispatch:
        def __init__(self, settings, config, **kw):
            assert config is cfg and config.vault.output_dir == str(output)
            assert settings['show_title'] == 'Configured English Show'
        def bind_run(self, scratch, **kw):
            bound.append((scratch, kw))
    monkeypatch.setattr(openai_runtime, 'OpenAIDispatch', FakeDispatch)
    def execute(step, ctx, *args):
        executed.append(step['name'])
        assert ctx['output_language'] == 'en'
        if step['name'] == 'collect':
            (ctx['scratch_dir'] / 'finalize-result.json').write_text(json.dumps({'body': '中文'}))
            (ctx['scratch_dir'] / 'broadcast-script-2026-11-05.txt').write_text('中文')
        return None
    monkeypatch.setattr(runner, '_execute_step', execute)
    result = runner.run_pipeline('morning', date='2026-11-05', config=cfg, plugin_root=ROOT)
    assert result['status'] == 'halted' and result['failed_step'] == 'tts'
    assert 'tts' not in executed and 'publish-paths' not in executed
    assert bound[0][1]['date'] == '2026-11-05'
    assert '-morning-en-' in bound[0][0].name
    assert bound[0][0].exists()
