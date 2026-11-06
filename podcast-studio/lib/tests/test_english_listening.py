"""Offline full-length, listening clarity and faithful narration regressions."""
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.english import (length_settings, language_contract, check_listening,
                         check_retention, check_audio_duration)
from lib.openai_runtime import OpenAIError
from lib.openai_speech import speech_payload, cost_bound
from test_openai_runtime import setup, adapter, ENGLISH, synthetic_meaning_review


def test_full_length_is_default_and_short_test_is_explicit():
    assert length_settings({}) == (2600, 3250, 3900)
    with pytest.raises(ValueError, match='demo'):
        length_settings({'min_words':450,'target_words':520,'max_words':600})
    assert length_settings({'episode_kind':'demo'}) == (450,520,600)
    assert length_settings({'speaking_rate_wpm':120}) == (2400,3000,3600)
    with pytest.raises(ValueError):length_settings({'min_minutes':3})
    with pytest.raises(ValueError):length_settings({'speaking_rate_wpm':True})


def test_clear_language_keeps_numbers_caveats_and_the_argument():
    text = ('The reported gain was 1.9 times the earlier rate. '
            'That is a measured change in one benchmark. '
            'It does not prove that every researcher works twice as fast. '
            'We still need to check how the test was run.')
    assert check_listening(text)['ok']
    assert check_retention(text,text)['ok']
    assert not check_retention(text,'The test improved.')['ok']
    tangled = 'We ' + 'can explain the evidence and the limits of the result ' * 5 + '.'
    assert not check_listening(tangled)['ok']
    contract=language_contract({})
    for phrase in ['3250','2600','3900','first use','counterevidence','conditions/deadlines','Independent critique','written show notes']:
        assert phrase in contract


def test_duration_uses_measured_audio_not_just_manuscript_length():
    assert check_audio_duration(1200,{})['ok']
    assert check_audio_duration(1800,{})['ok']
    assert not check_audio_duration(217.65,{})['ok']
    assert not check_audio_duration(1801,{})['ok']
    assert not check_audio_duration(float('nan'),{})['ok']
    assert check_audio_duration(217.65,{'episode_kind':'demo'})['ok']


def test_full_narration_reservation_with_new_output_cap_is_bounded():
    words=('We check the source and explain what the result means. '*400).split()[:3250]
    payloads=[speech_payload(' '.join(words[i:i+80]),'marin') for i in range(0,len(words),80)]
    assert len(payloads)==41
    assert max(cost_bound(p) for p in payloads) < .10
    assert sum(cost_bound(p) for p in payloads) < 1.5
    assert all(p['response']['max_output_tokens']==1536 for p in payloads)
    assert '130 words per minute' in payloads[0]['response']['instructions']
    assert all('reasoning' not in p['response'] for p in payloads)


def test_full_missing_reader_stops_before_paid_call_and_does_not_rewrite(setup):
    calls=[];a=adapter(setup,lambda *args,**kwargs:calls.append(1))
    script=setup[0]/'scratch/broadcast-script-test.txt';script.write_text(ENGLISH)
    with pytest.raises(OpenAIError,match='finalized manuscript'):
        a.synthesize(setup[0]/'scratch',setup[0]/'scratch/audio.mp3')
    assert not calls and script.read_text()==ENGLISH
    assert not json.loads(Path(setup[1]['budget_ledger']).read_text())['reservations']


def test_full_synthetic_short_audio_is_retained_for_review_not_landed(setup):
    setup[1]['daily_limit_usd']=10  # Synthetic test ledger only.
    def transport(endpoint,payload,**kwargs):
        return {'pcm':b'\0\0'*2400,
                'transcript':payload['response']['input'][0]['content'][0]['text']}
    a=adapter(setup,transport);s=setup[0]/'scratch'
    (s/'broadcast-script-test.txt').write_text(ENGLISH)
    (s/'finalize-result.json').write_text(json.dumps({'title':'The Evidence','body':ENGLISH}))
    synthetic_meaning_review(s, ENGLISH)
    with pytest.raises(OpenAIError,match='duration'):
        a.synthesize(s,s/'audio.mp3')
    assert not (s/'audio.mp3').exists()
    assert (s/'audio-duration.json').exists()
    assert (s/'audio-disclosure.md').exists()
    assert (s/'broadcast-script-test.txt').read_text()==ENGLISH
    assert list(s.glob('.openai-speech-*/*.pcm'))
    assert len(json.loads(Path(setup[1]['budget_ledger']).read_text())['reservations'])==1


def meaning_fixture():
    reader='The result was 1.9 times the earlier rate. It does not measure every kind of work. We should check the test before drawing a broad conclusion.'
    pairs=[{'category':'fact','reader_quote':'The result was 1.9 times the earlier rate.','spoken_quote':'The result was 1.9 times the earlier rate.'},
           {'category':'caveat','reader_quote':'It does not measure every kind of work.','spoken_quote':'It does not measure every kind of work.'},
           {'category':'reasoning_or_opinion','reader_quote':'We should check the test before drawing a broad conclusion.','spoken_quote':'We should check the test before drawing a broad conclusion.'}]
    return reader,{'accessible_language':True,'meaning_preserved':True,'issues':[],'evidence':pairs}


def test_independent_review_requires_real_quotes_and_caveat_coverage():
    from lib.english import validate_listening_review
    reader,review=meaning_fixture()
    assert validate_listening_review(reader,reader,review)['ok']
    assert not validate_listening_review(reader,reader,None)['ok']
    assert not validate_listening_review(reader,reader,{**review,'meaning_preserved':False})['ok']
    bad=json.loads(json.dumps(review));bad['evidence'][1]['spoken_quote']='An invented caveat that was never said.'
    assert not validate_listening_review(reader,reader,bad)['ok']
    assert not validate_listening_review(reader,reader,{**review,'evidence':review['evidence'][:1]})['ok']


def test_english_meaning_failure_halts_even_with_advisory_scorecard(setup):
    from lib.runner import _scorecard_step
    reader,review=meaning_fixture();s=setup[0]/'scratch';out=setup[0]/'out'
    (s/'finalize-result.json').write_text(json.dumps({'title':'The Test','body':reader}))
    (s/'broadcast-script-2026-11-05.txt').write_text(reader)
    def dispatch(agent,prompt,scratch,artifact,**kwargs):
        assert 'COMPLETE finalized manuscript' in prompt and 'Spoken rewrite (DATA)' in prompt
        verdict={'有观点':3,'有温度':3,'不同质化':3,'说明':'Synthetic review',
                 'listening_review':{**review,'meaning_preserved':False}}
        (Path(scratch)/artifact).write_text(json.dumps(verdict))
        return {'ok':True}
    ctx={'scratch_dir':s,'output_dir':out,'state_dir':out/'state','reports_dir':out,
         'show':'morning','date':'2026-11-05','plugin_root':Path(__file__).resolve().parents[2],
         'output_language':'en','public_only':True,'enforce_scorecard':False}
    result=_scorecard_step({'name':'scorecard','agent':'scorecard','artifact':'scorecard-verdict.json'},ctx,dispatch)
    assert result['status']=='halted' and result['failed_step']=='scorecard'


@pytest.mark.parametrize('review_state',['missing','stale'])
def test_full_unreviewed_or_changed_text_cannot_start_paid_narration(setup,review_state):
    calls=[];a=adapter(setup,lambda *args,**kwargs:calls.append(1));s=setup[0]/'scratch'
    (s/'broadcast-script-test.txt').write_text(ENGLISH)
    (s/'finalize-result.json').write_text(json.dumps({'title':'The Test','body':ENGLISH}))
    if review_state=='stale':
        synthetic_meaning_review(s, ENGLISH)
        (s/'broadcast-script-test.txt').write_text(ENGLISH+' We should read the source before we accept the answer.')
    with pytest.raises(OpenAIError,match='review'):
        a.synthesize(s,s/'audio.mp3')
    assert not calls and not json.loads(Path(setup[1]['budget_ledger']).read_text())['reservations']
