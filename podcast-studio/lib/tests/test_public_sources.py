"""Public news/archive source policy and explicit shorter English output."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess

import pytest
from test_openai_runtime import setup, adapter, invoke, response, ROOT
from lib.openai_runtime import OpenAIError
from lib.public_sources import PublicSources
from lib.english import check_english, language_contract
from lib.runner import _call_gate, _continuity_read, _build_step_prompt, _assemble_briefs


def public_setup(setup):
    base, settings, config = setup
    news, archive = base/'news/item.md', base/'notes/archive.md'
    news.write_text('Public news original source and its limitations.')
    archive.write_text('Public original article about checking evidence.')
    files=[{'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
            'kind':kind,'title':kind,'url':'https://example.org/'+kind,'published':'2026-11-05'}
           for p,kind in [(news,'news'),(archive,'archive')]]
    manifest=base/'manifest.json'
    manifest.write_text(json.dumps({'version':1,'files':files,'candidates':[
        {'domain':d,'topic_tag':d,'source_path':str(news.resolve())}
        for d in ['tech','market','science','geo','culture']],
        'archive_context':{'source_path':str(archive.resolve()),'question':'Who verifies the evidence?'}}))
    settings.update(episode_kind='demo',source_mode='public',source_manifest=str(manifest),source_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),min_words=450,target_words=520,max_words=600)
    return manifest,news,archive


def test_manifest_exact_files_rejects_private_voice_and_changed_sources(setup):
    manifest,news,archive=public_setup(setup)
    a=adapter(setup)
    scratch=setup[0]/'scratch'
    private=setup[0]/'out/state';private.mkdir()
    bible=private/'character-bible.md';bible.write_text('PRIVATE-VOICE-SENTINEL')
    with pytest.raises(OpenAIError): a._local_tool('read_file',{'path':str(bible),'offset':0},scratch,ROOT,'davinci')
    assert 'Public news' in a._local_tool('read_file',{'path':str(news),'offset':0},scratch,ROOT,'davinci')
    assert a.roots==[]
    extra=news.parent/'private.md';extra.write_text('PRIVATE-NOTE')
    listing=a._local_tool('list_files',{'path':str(news.parent),'offset':0},scratch,ROOT,'davinci')
    assert str(news) in listing and 'private.md' not in listing
    news.write_text('changed')
    with pytest.raises(ValueError,match='changed'): a.public.verify()


def test_public_scratch_does_not_expose_internal_receipts_or_private_scorecard(setup):
    public_setup(setup);a=adapter(setup);scratch=setup[0]/'scratch'
    for name in ['.openai-response-abc.json','scorecard-verdict.json','character-bible.md','arbitrary.txt']:
        (scratch/name).write_text('PRIVATE')
        with pytest.raises(OpenAIError):a._path(str(scratch/name),scratch)
    (scratch/'draft-A.md').write_text('public draft')
    assert a._path(str(scratch/'draft-A.md'),scratch)==(scratch/'draft-A.md').resolve()


def test_public_prep_uses_real_existing_command_without_private_note_or_vault_reads(setup):
    public_setup(setup);a=adapter(setup)
    args={'domain':'philosophy','contrarian':'lesswrong','date':'2026-11-05','show':'morning',
          'candidates_json':'[]','pkos_note_json':json.dumps({'id':'private-model-invention'})}
    brief=json.loads(a._local_tool('prep_check',args,setup[0]/'scratch',ROOT,'davinci'))
    assert not brief.get('error') and brief['pkos_note'] is None
    assert brief['source_mode']=='public' and brief['archive_context']['url'].startswith('https://')
    assert brief['cross_domain_candidates']==brief['self_past_candidates']==brief['ief_candidates']==[]
    assert set(x['topic_tag'] for x in brief['approved_topics'])=={'tech','market','science','geo','culture'}


def test_public_prep_evening_and_legacy_requirement_are_distinct(setup,monkeypatch):
    public_setup(setup);a=adapter(setup)
    scripts=ROOT/'skills/podcast-studio-prep/scripts';monkeypatch.syspath_prepend(str(scripts))
    spec=importlib.util.spec_from_file_location('public_prep',scripts/'orchestrator.py')
    orch=importlib.util.module_from_spec(spec);spec.loader.exec_module(orch)
    monkeypatch.setattr(orch,'load_pkos_notes',lambda *a,**kw:pytest.fail('private vault read'))
    assert orch.run_check([],str(setup[0]/'out/topic_log.yaml'),'2026-11-05')['error']
    result=orch.run_check(a.public.candidates,str(setup[0]/'out/topic_log.yaml'),'2026-11-05',
        source_mode='public',archive_context=a.public.archive_context(),show_type='evening')
    assert result['pkos_note'] is None and result['open_questions'][0]['title']=='Who verifies the evidence?'


def test_public_continuity_is_local_and_model_briefs_do_not_contain_it(setup,monkeypatch):
    from lib import runner
    private={'claim':'PRIVATE-BET-SENTINEL'}
    monkeypatch.setattr(runner,'load_cards',lambda *a:[{'episode':{'date':'2026-11-01'}}])
    monkeypatch.setattr(runner,'due_bets',lambda *a:[private])
    monkeypatch.setattr(runner,'carried_open_questions',lambda *a:['PRIVATE-QUESTION'])
    ctx={'date':'2026-11-05','show':'morning','public_only':True,'scratch_dir':setup[0]/'scratch',
         'output_dir':setup[0]/'out','config':setup[2]}
    result=_continuity_read(ctx)
    assert result['due_bets']==[] and ctx['private_continuity_local']['due_bets']==[private]
    assert 'PRIVATE' not in (setup[0]/'scratch/continuity.json').read_text()
    ctx['continuity']=result
    _assemble_briefs(ctx)
    assert 'PRIVATE' not in (setup[0]/'scratch/writing-brief-A.json').read_text()
    prompt=_build_step_prompt({'name':'finalize','agent':'kuaidao','inputs':['character-bible.md'],'artifact':'finalize-result.json'},ctx,None)
    assert 'character-bible' not in prompt


def test_short_length_is_explicit_and_checked_by_adapter_runner_and_scorecard(setup):
    public_setup(setup)
    short=('We have a choice in the way we make this work. '*45).strip()
    assert not check_english(short)['ok']
    assert check_english(short,min_words=450,max_words=600)['ok']
    assert 'target 520 English words' in language_contract(setup[1])
    a=adapter(setup,lambda *a,**kw:response(json.dumps({'title':'The Evidence','body':short})))
    assert invoke(a,setup,artifact='finalize-result.json',step='finalize')['ok']
    path=setup[0]/'scratch/draft-A.md';path.write_text(short)
    assert _call_gate('check_min_chars',lambda *a,**kw:pytest.fail('Chinese floor used'),path,{},
        {'output_language':'en','english_min_words':450,'english_max_words':600})['ok']
    from lib.scorecard import _english_duration
    assert _english_duration(short,450,600)['ok']
    assert not _english_duration(short+' '+short,450,600)['ok']


def test_short_narration_reservation_and_real_merge_stay_under_caps(setup):
    public_setup(setup)
    setup[1]['daily_limit_usd']=2
    text=('We have a choice in the way we make this work. '*45).strip()
    script=setup[0]/'scratch/broadcast-script-test.txt';script.write_text(text)
    calls=[]
    def transport(endpoint,payload,**kw):
        calls.append(1)
        return {'pcm':b'\0\0'*2400,'transcript':payload['response']['input'][0]['content'][0]['text']}
    a=adapter(setup,transport);a.synthesize(setup[0]/'scratch',setup[0]/'scratch/audio.mp3')
    ledger=json.loads(Path(setup[1]['budget_ledger']).read_text())
    assert len(calls)<=8 and ledger['reservations'][0]['reserved_usd']<.8
    assert .5+sum(r['reserved_usd'] for r in ledger['reservations'])<=2
    a.synthesize(setup[0]/'scratch',setup[0]/'scratch/audio.mp3')
    assert len(ledger['reservations'])==1


def test_collect_real_prep_round_trip_transmits_only_pinned_public_inputs(setup):
    public_setup(setup)
    private=setup[0]/'notes/private-essay.md';private.write_text('PRIVATE-ESSAY-SENTINEL')
    payloads=[]
    def transport(endpoint,payload,**kw):
        payloads.append(json.loads(json.dumps(payload)))
        if len(payloads)==1:
            return {'status':'completed','output':[
                {'type':'function_call','name':'prep_check','call_id':str(i),'arguments':json.dumps({
                    'date':'2026-11-05','show':'morning','domain':d,'contrarian':c,
                    'candidates_json':'[]','pkos_note_json':'null'})}
                for i,(d,c) in enumerate([('philosophy','lesswrong'),('cognition','marginal-revolution'),('history','stratechery')])]}
        return response('## 当日新闻背景\n- **Public**: A checked source. (source: https://example.org/news, 2026-11-05)')
    a=adapter(setup,transport)
    assert invoke(a,setup,step='collect')['ok']
    assert 'PRIVATE-ESSAY-SENTINEL' not in json.dumps(payloads)
    final=(setup[0]/'scratch/material-summary.md').read_text()
    assert final.count('"source_mode": "public"')==3
    assert '"pkos_note": null' in final


def test_public_postpublish_uses_current_public_artifacts_not_private_stores(setup):
    public_setup(setup)
    ctx={'public_only':True,'scratch_dir':setup[0]/'scratch','date':'2026-11-05','show':'morning'}
    prompt=_build_step_prompt({'name':'thesis-distill','agent':'thesis-distiller',
        'inputs':['published.md','stance-card.yaml','thesis-ledger.yaml'],'artifact':'thesis-apparatus.json'},ctx,None)
    assert 'finalize-result.json' in prompt and 'stance-card.json' in prompt
    assert 'thesis-ledger.yaml' not in prompt


def test_disclosed_maximum_script_has_same_scorecard_and_resume_length_gate():
    from lib.scorecard import _english_duration
    from lib.english import narration_body
    text=' '.join(['We', 'have', 'a', 'choice']*150)
    disclosed='This episode uses an AI-generated voice.\n\n'+text
    assert check_english(narration_body(disclosed),min_words=450,max_words=600)['ok']
    assert _english_duration(disclosed,450,600)['ok']


def test_configuration_pins_manifest_before_a_changed_scope_is_read(setup):
    manifest,news,archive=public_setup(setup)
    data=json.loads(manifest.read_text());data['files'][0]['path']=str(setup[0]/'private.md')
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='configuration hash'):adapter(setup)


def test_public_run_cannot_reuse_unmarked_nonempty_scratch(setup):
    public_setup(setup);a=adapter(setup)
    (setup[0]/'scratch/draft-A.md').write_text('private previous artifact')
    with pytest.raises(OpenAIError,match='unmarked'):
        a.bind_run(setup[0]/'scratch',resume=False)


@pytest.mark.parametrize('defect',['missing-title','blank-title','empty-context'])
def test_invalid_public_context_is_rejected_before_any_provider_call(setup,defect):
    manifest,news,archive=public_setup(setup)
    data=json.loads(manifest.read_text())
    if defect=='missing-title':data['files'][1].pop('title')
    elif defect=='blank-title':data['files'][1]['title']=' '
    else:
        archive.write_text('---\ntitle: only metadata\n---\n')
        data['files'][1]['sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data));setup[1]['source_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(ValueError):adapter(setup,lambda *a,**kw:pytest.fail('provider called'))
    assert not json.loads(Path(setup[1]['budget_ledger']).read_text())['reservations']
