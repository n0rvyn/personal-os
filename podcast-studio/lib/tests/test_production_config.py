"""Existing config creation, bilingual paths and non-deprecated narration contracts."""
import base64
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.config import ConfigError, initialize_config, load_config
from lib.episode import episode_paths, check_stance_card
from lib.openai_runtime import settings_from_config, OpenAIError
from lib.openai_speech import MODEL, cost_bound, speech_payload, request_realtime


def raw_config(tmp_path):
    return f'''vault:
  subjective_dir: {tmp_path}
  news_dir: {tmp_path}
  output_dir: {tmp_path}
tts:
  provider: minimax
  host_voice: original-voice
'''


def test_new_project_initialization_requires_and_stores_channel_name(tmp_path):
    template, target = tmp_path / 'template.yaml', tmp_path / 'config.yaml'
    template.write_text(raw_config(tmp_path))
    original = template.read_bytes()
    for name in (None, '', '  ', 'name\ninstruction'):
        with pytest.raises(ConfigError):
            initialize_config(target, name=name, template_path=template)
        assert not target.exists()
    initialize_config(target, name='A configured show', template_path=template)
    assert load_config(target).podcast.name == 'A configured show'
    assert template.read_bytes() == original
    with pytest.raises(FileExistsError):
        initialize_config(target, name='Do not overwrite', template_path=template)


def test_old_unnamed_project_is_compatible_and_never_rewritten(tmp_path):
    config = tmp_path / 'config.yaml'
    config.write_text(raw_config(tmp_path))
    original = config.read_bytes()
    cfg = load_config(config)
    assert cfg.podcast is None and cfg.production is None
    assert settings_from_config(cfg) is None
    assert config.read_bytes() == original


def test_same_config_selects_provider_language_and_display_name(tmp_path):
    p = tmp_path / 'config.yaml'
    p.write_text(raw_config(tmp_path) + '''podcast:
  name: 原有节目
  english_name: Configured English Show
production:
  provider: openai
  language: en
  source_authorized: false
''')
    cfg = load_config(p)
    settings = settings_from_config(cfg)
    assert settings['show_title'] == 'Configured English Show'
    assert settings['speech_model'] == MODEL
    assert cfg.vault.output_dir == str(tmp_path)
    assert cfg.vault.episodes_dir == str(tmp_path / 'episodes')
    assert cfg.vault.state_dir == str(tmp_path / 'state')
    assert settings['source_authorized'] is False
    assert sorted(q.name for q in tmp_path.iterdir() if q.is_dir()) == ['episodes', 'reports', 'state']
    assert 'By the Way' not in (Path(__file__).resolve().parents[1] / 'openai_runtime.py').read_text()


def test_openai_generation_requires_configured_name_and_rejects_output_override(tmp_path):
    p = tmp_path / 'config.yaml'
    p.write_text(raw_config(tmp_path) + 'production:\n  provider: openai\n  language: en\n')
    with pytest.raises(ConfigError, match='podcast.name'):
        load_config(p)
    p.write_text(raw_config(tmp_path) + '''podcast:
  name: Existing Show
production:
  provider: openai
  language: en
  english_output_dir: invented
''')
    with pytest.raises(OpenAIError, match='existing vault.output_dir'):
        settings_from_config(load_config(p))


def test_same_episode_folder_separates_language_filenames(tmp_path):
    zh = episode_paths(tmp_path, '2026-11-05', 'Same title', 'morning')
    en = episode_paths(tmp_path, '2026-11-05', 'Same title', 'morning', language='en')
    assert zh['script'].parent == en['script'].parent == tmp_path
    assert zh['script'].name == '2026-11-05-Same title.md'
    assert en['script'].name == '2026-11-05-Same title-en.md'
    assert zh['stance'].name == '2026-11-05-morning.stance.yaml'
    assert en['stance'].name == '2026-11-05-morning-en.stance.yaml'


def test_language_scoped_stance_gate_reuses_shared_history(tmp_path):
    from lib.stance import write_card, load_cards
    today = dt.date.today().isoformat()
    card = {'episode': {'date': today, 'show': 'morning'}, 'bets': [], 'open_questions': ['original']}
    write_card(tmp_path, today, 'morning', card)
    assert check_stance_card(tmp_path, today, 'morning')['ok']
    assert not check_stance_card(tmp_path, today, 'morning', language='en')['ok']
    card = {**card, 'episode': {**card['episode'], 'language': 'en'}}
    write_card(tmp_path, today, 'morning', card)
    assert check_stance_card(tmp_path, today, 'morning', language='en')['ok']
    assert len(load_cards(tmp_path)) == 2


class FakeSocket:
    def __init__(self, events):
        self.events, self.sent, self.closed = iter(events), [], False
    def recv(self): return json.dumps(next(self.events))
    def send(self, value): self.sent.append(json.loads(value))
    def settimeout(self, value): assert value > 0
    def close(self): self.closed = True


def test_realtime_narration_protocol_and_hard_token_budget(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    events = [{'type': 'session.created'}, {'type': 'session.updated'},
              {'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'\0\0' * 240).decode()},
              {'type': 'response.output_audio_transcript.done', 'transcript': 'Read this exactly.'},
              {'type': 'response.done', 'response': {'status': 'completed'}}]
    ws, connections = FakeSocket(events), []
    def connect(url, **kwargs):
        connections.append((url, kwargs))
        return ws
    payload = speech_payload('Read this exactly.', 'marin')
    result = request_realtime(payload, connect=connect)
    assert connections[0][0] == 'wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1-mini'
    assert connections[0][1]['redirect_limit'] == 0
    assert len(ws.sent) == 2 and ws.sent[-1] == payload
    assert ws.sent[0]['session']['audio']['input']['turn_detection'] is None
    assert payload['response']['conversation'] == 'none'
    assert payload['response']['max_output_tokens'] == 4096
    assert payload['response']['audio']['output']['format'] == {'type': 'audio/pcm', 'rate': 24000}
    assert result['transcript'] == 'Read this exactly.' and result['pcm'] == b'\0\0' * 240
    assert cost_bound(payload) >= 4096 * 20 / 1_000_000
    assert ws.closed


def test_realtime_incomplete_is_rejected_and_connection_closed(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    ws = FakeSocket([{'type': 'session.created'}, {'type': 'session.updated'},
                     {'type': 'response.done', 'response': {'status': 'incomplete'}}])
    with pytest.raises(ValueError, match='incomplete'):
        request_realtime(speech_payload('A short script.', 'marin'), connect=lambda *a, **kw: ws)
    assert ws.closed


def test_existing_project_config_is_discovered_without_new_command(tmp_path, monkeypatch):
    import lib.config as config_module
    p = tmp_path / 'config.yaml'
    p.write_text(raw_config(tmp_path))
    child = tmp_path / 'child'
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.delenv('PODCAST_STUDIO_CONFIG', raising=False)
    monkeypatch.setattr(config_module, '_resolve_personal_os_yaml', lambda: None)
    cfg = load_config()
    assert cfg.vault.output_dir == str(tmp_path)
    explicit = tmp_path / 'explicit.yaml'
    explicit.write_text(raw_config(tmp_path) + 'podcast:\n  name: Explicit project\n')
    monkeypatch.setenv('PODCAST_STUDIO_CONFIG', str(explicit))
    assert load_config().podcast.name == 'Explicit project'


def test_english_card_can_settle_same_day_chinese_bet_without_mutating_it(tmp_path):
    from lib.stance import write_card, new_bet_id
    today = dt.date.today().isoformat()
    bet_id = new_bet_id(today, 'morning', 1)
    zh = {'episode': {'date': today, 'show': 'morning'},
          'bets': [{'id': bet_id, 'claim': '原有判断', 'horizon': '1d', 'settle_by': today, 'status': 'open'}],
          'open_questions': []}
    original = write_card(tmp_path, today, 'morning', zh)
    original_bytes = original.read_bytes()
    en = {'episode': {'date': today, 'show': 'morning', 'language': 'en'}, 'bets': [],
          'open_questions': [], 'settles': [{'ref': bet_id, 'verdict': 'hit', 'evidence': 'Recorded source'}]}
    written = write_card(tmp_path, today, 'morning', en)
    assert written.is_file() and original.read_bytes() == original_bytes


def test_english_run_reuses_existing_character_bible(tmp_path):
    from lib.runner import _bible_distill_step
    state, scratch = tmp_path / 'state', tmp_path / 'scratch'
    state.mkdir(); scratch.mkdir()
    bible = state / 'character-bible.md'
    bible.write_text('原有声音与立场')
    def never_dispatch(*args, **kwargs):
        raise AssertionError('Existing shared Bible must not be overwritten by English distillation')
    ctx = {'scratch_dir': scratch, 'plugin_root': tmp_path, 'config': None,
           'state_dir': str(state), 'output_language': 'en'}
    assert _bible_distill_step({'name': 'bible-distill'}, ctx, never_dispatch) is None
    assert bible.read_text() == '原有声音与立场'


def test_post_publish_distiller_receives_this_language_script(tmp_path):
    from lib.runner import _build_step_prompt
    zh = tmp_path / '2026-11-05-AAA.md'
    en = tmp_path / '2026-11-05-ZZZ-en.md'
    zh.write_text('Chinese historical episode')
    en.write_text('English finalized episode')
    ctx = {'scratch_dir': tmp_path, 'episodes_dir': str(tmp_path), 'show': 'morning',
           'date': '2026-11-05', 'output_language': 'en', 'published_script_path': en}
    prompt = _build_step_prompt({'name': 'coveredground-distill', 'agent': 'coveredground-distiller',
                                 'inputs': ['published.md'], 'artifact': 'coveredground-apparatus.json'}, ctx, None)
    assert str(en) in prompt and str(zh) not in prompt


def test_ordinary_runner_cli_has_no_separate_english_activation():
    import subprocess, os
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONPATH': str(root) + os.pathsep + os.environ.get('PYTHONPATH', '')}
    result = subprocess.run([sys.executable, '-m', 'lib.runner', '--help'],
                            env=env, cwd=root, text=True, capture_output=True, check=True)
    assert '--show' in result.stdout
    assert '--openai-profile' not in result.stdout and '--authorize-openai-sources' not in result.stdout
    assert 'By the Way' not in result.stdout


def test_initialization_cli_missing_name_creates_nothing(tmp_path):
    import subprocess, os
    root = Path(__file__).resolve().parents[2]
    template, target = tmp_path / 'template.yaml', tmp_path / 'new-config.yaml'
    template.write_text(raw_config(tmp_path))
    env = {**os.environ, 'PYTHONPATH': str(root) + os.pathsep + os.environ.get('PYTHONPATH', '')}
    result = subprocess.run([sys.executable, '-m', 'lib.config', '--init', str(target),
                             '--from-config', str(template)], env=env, cwd=root,
                            text=True, capture_output=True)
    assert result.returncode == 1 and 'podcast.name' in result.stderr
    assert not target.exists()
