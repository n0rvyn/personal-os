from test_openai_runtime import synthetic_meaning_review
"""Independent-review regressions. All provider responses and sources are synthetic."""
import concurrent.futures
import datetime as dt
import importlib.util
import json
from pathlib import Path
import threading

import pytest
import jsonschema
from test_openai_runtime import setup, adapter, invoke, response, ROOT, ENGLISH
from test_production_config import FakeSocket
from lib import openai_runtime as runtime
from lib.english import check_english_title
from lib.openai_runtime import Budget, BudgetError, OpenAIError
from lib.openai_speech import narration_words, request_realtime, speech_payload
from lib.runner import _publish_step, RunnerError


def test_evening_collect_accepts_actual_evening_prep_contract(setup, monkeypatch):
    scripts = ROOT / 'skills/podcast-studio-prep/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('review_prep', scripts / 'orchestrator.py')
    orch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(orch)
    monkeypatch.setattr(orch, '_contrarian_pull', lambda **kw: {'source': 'synthetic'})
    brief = orch.run_check([], str(setup[0] / 'out/topic_log.yaml'), '2026-11-05',
        pkos_note={'id': 'synthetic', 'title': '原文', 'excerpt': '原文保持不变。'},
        vault_root=str(setup[0] / 'notes'), show_type='evening', seed=1)
    assert 'approved_topics' not in brief
    assert isinstance(brief['open_questions'], list) and isinstance(brief['evidence'], list)
    calls = []
    def transport(endpoint, payload, **kw):
        calls.append(1)
        if len(calls) == 1:
            return {'status': 'completed', 'output': [
                {'type': 'function_call', 'name': 'prep_check', 'call_id': str(i),
                 'arguments': json.dumps({'show': 'evening', 'domain': domain, 'contrarian': source})}
                for i, (domain, source) in enumerate([('philosophy', 'lesswrong'),
                    ('cognition', 'marginal-revolution'), ('history', 'stratechery')])]}
        return response('## 当日新闻背景\nSynthetic source evidence in its original language.')
    a = adapter(setup, transport)
    monkeypatch.setattr(a, '_local_tool', lambda *args: json.dumps(brief))
    assert invoke(a, setup, step='collect')['ok']
    assert (setup[0] / 'scratch/material-summary.md').read_text().count('"open_questions"') == 3
    assert not list((setup[0] / 'scratch').glob('.openai-conversation-*'))


def test_legacy_title_collision_cannot_overwrite_english_publication(setup):
    root = setup[0]
    (root / 'out/episodes').mkdir()
    scratch = root / 'scratch'
    final = scratch / 'finalize-result.json'
    final.write_text(json.dumps({'title': 'Policy', 'body': ENGLISH}))
    (scratch / 'audio-files.mp3').write_bytes(b'synthetic-English-audio')
    ctx = {'output_dir': root / 'out', 'scratch_dir': scratch, 'date': '2026-11-05',
           'show': 'morning', 'output_language': 'en', 'no_tts': False}
    (scratch / 'audio-disclosure.md').write_text('Voice disclosure: AI-generated voice, not a human narrator.')
    paths = _publish_step(ctx)
    before = {k: paths[k].read_bytes() for k in ('script', 'audio')}
    final.write_text(json.dumps({'title': 'Policy-en', 'body': '原有中文节目'}))
    (scratch / 'audio-files.mp3').write_bytes(b'other-audio')
    with pytest.raises((RunnerError, OpenAIError), match='overwrite|exists|different'):
        _publish_step({**ctx, 'output_language': 'zh'})
    assert all(paths[k].read_bytes() == v for k, v in before.items())


def test_budget_day_rollover_stops_remaining_pre_reserved_speech(setup, monkeypatch):
    setup[1]['daily_limit_usd'] = 10
    today = dt.datetime.now(dt.timezone.utc).replace(hour=23, minute=59)
    clock = [today]
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None): return clock[0].astimezone(tz)
    monkeypatch.setattr(runtime.dt, 'datetime', Clock)
    calls = []
    def transport(endpoint, payload, **kw):
        calls.append(1)
        clock[0] += dt.timedelta(days=1)
        return {'pcm': b'\0\0' * 240, 'transcript': payload['response']['input'][0]['content'][0]['text']}
    a = adapter(setup, transport)
    (setup[0] / 'scratch/broadcast-script-test.txt').write_text(ENGLISH)
    (setup[0] / 'scratch/finalize-result.json').write_text(json.dumps({'title':'Title','body':ENGLISH}))
    synthetic_meaning_review(setup[0] / 'scratch', ENGLISH)
    with pytest.raises(BudgetError, match='date changed'):
        a.synthesize(setup[0] / 'scratch', setup[0] / 'scratch/audio.mp3')
    assert len(calls) == 1
    assert len(json.loads(Path(setup[1]['budget_ledger']).read_text())['reservations']) == 1


def test_realtime_rechecks_budget_after_handshake_before_generation(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    socket = FakeSocket([{'type': 'session.created'}, {'type': 'session.updated'}])
    def deny(): raise BudgetError('Budget date changed')
    with pytest.raises(BudgetError):
        request_realtime(speech_payload('Synthetic sentence.', 'marin'),
                         connect=lambda *a, **kw: socket, before_send=deny)
    assert [x['type'] for x in socket.sent] == ['session.update']
    assert socket.closed


def test_budget_receipt_must_still_exist_and_fit_actual_commitments(setup):
    budget = Budget(setup[1])
    receipt = budget.reserve([.01], 'synthetic')
    budget.verify(receipt)
    ledger = Path(setup[1]['budget_ledger'])
    data = json.loads(ledger.read_text())
    data['opening_committed_usd'] = 2
    ledger.write_text(json.dumps(data))
    with pytest.raises(BudgetError, match='exceed'): budget.verify(receipt)
    data['opening_committed_usd'] = .5
    data['reservations'] = []
    ledger.write_text(json.dumps(data))
    with pytest.raises(BudgetError, match='no longer present'): budget.verify(receipt)


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit])
def test_interrupted_transport_has_durable_receipt_and_cannot_repeat(setup, interruption):
    calls = []
    def transport(*args, **kw):
        calls.append(1)
        assert len(list((setup[0] / 'scratch').glob('.openai-flight-*.json'))) == 1
        raise interruption()
    with pytest.raises(interruption): invoke(adapter(setup, transport), setup)
    assert list((setup[0] / 'scratch').glob('.openai-flight-*.json'))
    result = invoke(adapter(setup, transport), setup)
    assert not result['ok'] and len(calls) == 1
    assert len(json.loads(Path(setup[1]['budget_ledger']).read_text())['reservations']) == 1


def test_interrupt_after_response_retains_response_and_blocks_rebilling(setup, monkeypatch):
    original = runtime._atomic
    def interrupt(path, value):
        if Path(path).name == 'material-summary.md': raise KeyboardInterrupt()
        original(path, value)
    monkeypatch.setattr(runtime, '_atomic', interrupt)
    with pytest.raises(KeyboardInterrupt): invoke(adapter(setup), setup)
    assert list((setup[0] / 'scratch').glob('.openai-response-*.json'))
    assert list((setup[0] / 'scratch').glob('.openai-flight-*.json'))
    assert not invoke(adapter(setup), setup)['ok']


def test_parallel_stations_do_not_mistake_active_receipts_for_crashes(setup):
    barrier = threading.Barrier(2)
    def transport(*args, **kw):
        barrier.wait(timeout=5)
        return response('Synthetic independent artifact')
    a = adapter(setup, transport)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: invoke(a, setup, artifact=f'candidate-{i}.md'), range(2)))
    assert all(r['ok'] for r in results)
    assert not list((setup[0] / 'scratch').glob('.openai-flight-*'))
    assert not (setup[0] / 'scratch/.openai-request-review.json').exists()


def test_final_title_gate_applies_before_artifact_and_at_publication(setup):
    a = adapter(setup, lambda *a, **kw: response(json.dumps({'title': '中文标题', 'body': ENGLISH})))
    assert not invoke(a, setup, artifact='finalize-result.json', step='finalize')['ok']
    final = setup[0] / 'scratch/finalize-result.json'
    assert not final.exists()
    final.write_text(json.dumps({'title': '中文标题', 'body': ENGLISH}))
    with pytest.raises(RunnerError, match='title'):
        _publish_step({'output_dir': setup[0] / 'out', 'scratch_dir': setup[0] / 'scratch',
                      'date': '2026-11-05', 'show': 'morning', 'output_language': 'en', 'no_tts': True})
    assert check_english_title('Café Policy 2026') and not check_english_title('123')


def test_narration_comparison_preserves_word_boundaries(setup):
    assert narration_words('We are now here.') != narration_words('We are nowhere.')
    assert narration_words("It's HERE!") == narration_words('it’s here.')
    a = adapter(setup, lambda *a, **kw: {'pcm': b'\0\0' * 240, 'transcript': 'We are nowhere.'})
    setup[1]['daily_limit_usd'] = 10
    # Adapter budget is instantiated already; this fixture explicitly raises its synthetic cap.
    a.budget.daily = 10
    (setup[0] / 'scratch/broadcast-script-test.txt').write_text(ENGLISH)
    (setup[0] / 'scratch/finalize-result.json').write_text(json.dumps({'title':'Title','body':ENGLISH}))
    synthetic_meaning_review(setup[0] / 'scratch', ENGLISH)
    with pytest.raises(OpenAIError, match='does not match'):
        a.synthesize(setup[0] / 'scratch', setup[0] / 'scratch/audio.mp3')
    assert not (setup[0] / 'scratch/audio.mp3').exists()


def test_schema_accepts_legacy_config_but_requires_name_for_openai():
    schema = json.loads((ROOT / '.config-ui/config.schema.json').read_text())
    legacy = {'vault': {'subjective_dir': '/notes', 'news_dir': '/news', 'output_dir': '/existing'},
              'tts': {'provider': 'minimax', 'host_voice': 'legacy'}}
    jsonschema.validate(legacy, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**legacy, 'production': {'provider': 'openai', 'language': 'en'}}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**legacy, 'podcast': {'name': ''}}, schema)


def test_failed_artifact_write_retains_known_result_without_rebilling(setup, monkeypatch):
    original = runtime._atomic
    calls = []
    def write(path, value):
        if Path(path).name == 'material-summary.md': raise OSError('synthetic full disk')
        original(path, value)
    def transport(*args, **kw):
        calls.append(1)
        return response('Synthetic known result')
    monkeypatch.setattr(runtime, '_atomic', write)
    assert not invoke(adapter(setup, transport), setup)['ok']
    assert list((setup[0] / 'scratch').glob('.openai-response-*.json'))
    assert list((setup[0] / 'scratch').glob('.openai-flight-*.json'))
    assert not invoke(adapter(setup, transport), setup)['ok']
    assert len(calls) == 1


def test_saved_tool_conversation_resumes_without_repeating_completed_tool(setup, monkeypatch):
    calls, tools = [], []
    def transport(endpoint, payload, **kw):
        calls.append(1)
        if len(calls) == 1:
            return {'status': 'completed', 'output': [{'type': 'function_call',
                'name': 'read_file', 'arguments': '{}', 'call_id': 'synthetic-read'}]}
        assert payload['input'][-1]['type'] == 'function_call_output'
        return response('Synthetic recovered artifact')
    a = adapter(setup, transport)
    def local(*args):
        tools.append(1)
        # Force the NEXT turn to stop before transport, leaving the tool journal.
        a.budget.daily = .5
        return 'Original source text'
    monkeypatch.setattr(a, '_local_tool', local)
    assert not invoke(a, setup)['ok']
    assert len(calls) == len(tools) == 1
    b = adapter(setup, transport)
    monkeypatch.setattr(b, '_local_tool', lambda *a: pytest.fail('completed tool repeated'))
    assert invoke(b, setup)['ok']
    assert len(calls) == 2 and not list((setup[0] / 'scratch').glob('.openai-conversation-*'))


def test_failed_sibling_during_reservation_blocks_waiting_transport(setup, monkeypatch):
    calls = []
    a = adapter(setup, lambda *args, **kw: calls.append(1))
    reserve = a.budget.reserve
    def reservation(*args):
        receipt = reserve(*args)
        (setup[0] / 'scratch/.openai-request-review.json').write_text('{}')
        return receipt
    monkeypatch.setattr(a.budget, 'reserve', reservation)
    assert not invoke(a, setup)['ok']
    assert not calls and not list((setup[0] / 'scratch').glob('.openai-flight-*'))


def test_collect_rejects_error_briefs_with_success_shaped_empty_topics(setup, monkeypatch):
    calls = []
    def transport(*args, **kw):
        calls.append(1)
        if len(calls) == 1:
            return {'status': 'completed', 'output': [
                {'type': 'function_call', 'name': 'prep_check', 'call_id': str(i),
                 'arguments': json.dumps({'domain': domain, 'contrarian': source, 'show': 'morning'})}
                for i, (domain, source) in enumerate([('philosophy', 'lesswrong'),
                    ('cognition', 'marginal-revolution'), ('history', 'stratechery')])]}
        return response('Synthetic material')
    a = adapter(setup, transport)
    monkeypatch.setattr(a, '_local_tool', lambda *args: json.dumps({'approved_topics': [],
        'error': 'pkos_note required', 'pkos_note': None}))
    assert not invoke(a, setup, step='collect')['ok']
    assert not (setup[0] / 'scratch/material-summary.md').exists()


def test_chinese_resume_excludes_english_scratch_and_rejects_explicit_reuse(setup):
    from lib.runner import _resolve_scratch_dir
    root, _, config = setup
    legacy = root / 'out/.scratch-2026-11-05-morning-original'
    english = root / 'out/.scratch-2026-11-05-morning-en-newest'
    legacy.mkdir()
    english.mkdir()
    (english / '.openai-run.json').write_text('{}')
    assert _resolve_scratch_dir(None, config, '2026-11-05', 'morning', resume=True) == legacy
    assert _resolve_scratch_dir(None, config, '2026-11-05', 'morning-en', resume=True) == english
    with pytest.raises(RunnerError, match='scratch'):
        _resolve_scratch_dir(english, config, '2026-11-05', 'morning', resume=True)
    # Arbitrary injected paths must not bypass identity based on folder name.
    generic = root / 'generic-recovery'
    generic.mkdir()
    (generic / '.openai-flight-interrupted.json').write_text('{}')
    with pytest.raises(RunnerError, match='scratch'):
        _resolve_scratch_dir(generic, config, '2026-11-05', 'morning', resume=True)
