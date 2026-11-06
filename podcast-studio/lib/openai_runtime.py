"""Opt-in OpenAI Responses + Speech adapter for the existing opinion DAG.

No SDK dependency, no persisted credentials, no arbitrary shell/tool execution.
Each HTTP request reserves a conservative cost in a shared locked ledger first.
Uncertain/failed requests keep their reservations; there are no automatic retries.
"""
from __future__ import annotations

import datetime as dt
import base64
import threading
import uuid
import fcntl
import hashlib
import json
import math
import os
from zoneinfo import ZoneInfo
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from lib.openai_speech import MODEL as SPEECH_MODEL, VOICES as VOICE_SET, speech_payload, cost_bound, request_realtime, narration_words

from lib.dispatch import AGENT_WHITELIST, DispatchError, _resolve_artifact
from lib.english import (LANGUAGE_CONTRACT, check_english, check_english_title, length_settings, language_contract, narration_body,
                         duration_settings, listening_settings, check_listening, check_retention, check_audio_duration, validate_listening_review)

TEXT_MODEL = 'gpt-4.1-mini-2025-04-14'



class OpenAIError(ValueError):
    pass


class RequestNotSent(OpenAIError):
    """A local preflight denied generation before the request was issued."""


class BudgetError(RequestNotSent):
    """A local pre-send budget denial, with no generation request issued."""


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise OpenAIError('Budget amounts must be finite nonnegative numbers')
    return float(value)


class Budget:
    """Ledger must already contain today's independently verified commitments.

    Conservative reservations count as spent even after success; this may stop
    early, but never silently frees an ambiguous paid request. All participating
    jobs must share this file. It is not an OpenAI account billing query.
    """
    def __init__(self, settings):
        self.timezone = ZoneInfo(settings['budget_timezone'])
        self.path = Path(settings['budget_ledger']).expanduser().resolve()
        self.per_call = _money(settings['per_call_limit_usd'])
        self.daily = _money(settings['daily_limit_usd'])
        if not self.per_call or not self.daily or not self.path.is_file():
            raise BudgetError('Positive approved limits and an existing verified budget ledger are required')

    def reserve(self, costs, purpose):
        costs = [_money(c) for c in costs]
        if not costs or any(c <= 0 or c > self.per_call for c in costs):
            raise BudgetError('Per-call cost limit exceeded (or invalid reservation)')
        today = dt.datetime.now(self.timezone).date().isoformat()
        with self.path.open('r+', encoding='utf-8') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            ledger = json.load(f)
            if (ledger.get('date') != today or ledger.get('timezone') != self.timezone.key
                    or ledger.get('commitments_verified') is not True):
                raise BudgetError('Actual daily commitments must be verified for today before paid calls')
            total = _money(ledger['opening_committed_usd'])
            records = ledger['reservations']
            if not isinstance(records, list):
                raise BudgetError('Malformed budget reservations')
            total += sum(_money(r['reserved_usd']) for r in records)
            if total + sum(costs) > self.daily:
                raise BudgetError('Daily cost limit exceeded including existing commitments')
            receipt = {'id': uuid.uuid4().hex, 'date': today, 'reserved_usd': sum(costs)}
            records.append({**receipt, 'purpose': purpose, 'reserved_usd': sum(costs),
                            'calls': len(costs), 'at': dt.datetime.now(dt.timezone.utc).isoformat()})
            f.seek(0)
            json.dump(ledger, f, indent=2)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
            return receipt

    def verify(self, receipt):
        """Recheck today's actual ledger before each previously reserved call."""
        today = dt.datetime.now(self.timezone).date().isoformat()
        with self.path.open('r', encoding='utf-8') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            ledger = json.load(f)
            if (receipt.get('date') != today or ledger.get('date') != today
                    or ledger.get('timezone') != self.timezone.key
                    or ledger.get('commitments_verified') is not True):
                raise BudgetError('Budget date changed or commitments are unverified; no new request authorized')
            records = ledger.get('reservations')
            if not isinstance(records, list) or not any(
                r.get('id') == receipt.get('id') and r.get('reserved_usd') == receipt.get('reserved_usd')
                for r in records if isinstance(r, dict)
            ):
                raise BudgetError('Budget reservation is no longer present; no request authorized')
            total = _money(ledger['opening_committed_usd']) + sum(_money(r['reserved_usd']) for r in records)
            if total > self.daily:
                raise BudgetError('Daily commitments now exceed the approved limit')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_api(endpoint, payload, *, timeout=120, before_send=None):
    """Fixed official origin; do not log payloads, keys or provider error bodies."""
    key = os.environ.get('OPENAI_API_KEY')
    if not key:
        raise OpenAIError('OPENAI_API_KEY is absent; provide it in the process environment')
    if endpoint == 'realtime':
        return request_realtime(payload, timeout=timeout, before_send=before_send)
    if endpoint != 'responses':
        raise OpenAIError('Unsupported OpenAI endpoint')
    req = urllib.request.Request('https://api.openai.com/v1/' + endpoint,
        data=json.dumps(payload).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        if before_send is not None:
            before_send()
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as response:
            content = response.read(16 * 1024 * 1024 + 1)
            if len(content) > 16 * 1024 * 1024:
                raise OpenAIError('OpenAI response exceeded local size limit')
            return json.loads(content)
    except urllib.error.HTTPError as e:
        raise OpenAIError(f'OpenAI HTTP {e.code}; reservation retained; no automatic retry') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OpenAIError('OpenAI transport failed; outcome uncertain; reservation retained') from None


def _atomic(path, content):
    """Artifact writes stay local and only become visible when complete."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(content if isinstance(content, bytes) else content.encode('utf-8'))
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def land_once(path, content):
    """Publish atomically without clobbering an existing episode; retries are idempotent."""
    data = content if isinstance(content, bytes) else content.encode('utf-8')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise OpenAIError('English publication path contains different existing content; no overwrite')
    finally:
        tmp.unlink(missing_ok=True)


def _tool(name, description, properties):
    return {'type': 'function', 'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties,
                           'required': list(properties), 'additionalProperties': False}, 'strict': True}


READ_TOOLS = [
    _tool('read_file', 'Read one approved UTF-8 note or artifact; use offset for pagination.',
          {'path': {'type': 'string'}, 'offset': {'type': 'integer'}}),
    _tool('list_files', 'List approved Markdown/JSON/YAML/text files in a directory.',
          {'path': {'type': 'string'}, 'offset': {'type': 'integer'}}),
]
PREP_TOOL = _tool('prep_check', 'Execute the real prep check. Call three times, one per route; embed returned briefs verbatim.',
    {'candidates_json': {'type': 'string'}, 'pkos_note_json': {'type': 'string'},
     'date': {'type': 'string'}, 'show': {'type': 'string', 'enum': ['morning', 'evening']},
     'domain': {'type': 'string', 'enum': ['philosophy', 'cognition', 'history']},
     'contrarian': {'type': 'string', 'enum': ['lesswrong', 'marginal-revolution', 'stratechery']}})


class OpenAIDispatch:
    def __init__(self, settings, config, *, authorize_sources=False, transport=request_api):
        if not authorize_sources:
            raise OpenAIError('Explicit per-run authorization to send selected sources to OpenAI is required')
        if transport is request_api and not os.environ.get('OPENAI_API_KEY'):
            raise OpenAIError('OPENAI_API_KEY is absent; no credentials were persisted')
        self.settings = dict(settings)
        self.min_words, self.target_words, self.max_words = length_settings(settings)
        self.contract = language_contract(settings)
        self.kind, self.pace, self.minute_bounds = duration_settings(settings)
        listening_settings(settings)
        if settings.get('source_mode', 'legacy') not in {'legacy', 'public'}:
            raise OpenAIError('Unsupported source_mode')
        self.public = None
        if settings.get('source_mode') == 'public':
            if settings.get('additional_read_roots'):
                raise OpenAIError('Public mode does not allow additional read roots')
            from lib.public_sources import PublicSources
            digest = settings.get('source_manifest_sha256')
            if not isinstance(digest, str) or len(digest) != 64:
                raise OpenAIError('Public mode requires the approved source_manifest_sha256')
            self.public = PublicSources(settings['source_manifest'], digest)
        if settings.get('model') != TEXT_MODEL or settings.get('speech_model') != SPEECH_MODEL:
            raise OpenAIError('Unpriced model: adapter only supports its verified text/Realtime model settings')
        if not isinstance(settings.get('show_title'), str) or not settings['show_title'].strip():
            raise OpenAIError('show_title must be a nonempty string')
        if settings.get('voice') not in VOICE_SET:
            raise OpenAIError('Unsupported speech voice')
        self.max_output = settings.get('max_output_tokens', 12000)
        if type(self.max_output) is not int or not 256 <= self.max_output <= 32768:
            raise OpenAIError('Invalid max_output_tokens')
        self.budget = Budget(settings)
        self.config = config
        self.transport = transport
        self._active_lock = threading.Lock()
        self._active_requests = set()
        # No whole home/plugin root read access. The config is the user's
        # explicitly selected data scope; scratch is added per dispatch.
        self.roots = [Path(p).expanduser().resolve() for p in (
            config.vault.subjective_dir, config.vault.news_dir,
            config.vault.output_dir, config.vault.root,
        ) if p]
        self.roots += [Path(p).expanduser().resolve() for p in settings.get('additional_read_roots', [])]
        if self.public:
            self.roots = []  # Exact files, never the whole vault/output tree.

    def _english(self, text):
        return check_english(text, min_words=self.min_words, max_words=self.max_words)

    def _public_scratch(self, path, scratch):
        import re
        if path.parent != Path(scratch).resolve():
            return False
        return bool(re.fullmatch(r'(material-summary\.md|continuity\.json|writing-brief-[ABC]\.json|draft-[ABC]\.md|polish-[ABC]\.md|critique-[ABC]\.json|magnitude-verdict\.json|score-verdict\.json|finalize-result\.json|factcheck-verdict\.json|broadcast-script-[0-9-]+\.txt|stance-card\.json|coveredground-apparatus\.json|thesis-apparatus\.json)', path.name))

    def bind_run(self, scratch, *, resume=False, date=None, show=None):
        """Prevent resuming incompatible provider/language/config artifacts."""
        self.run_date, self.run_show = date, show
        identity = {'date': date, 'show': show, 'provider': 'openai', 'language': 'en', 'model': TEXT_MODEL,
                    'speech_model': SPEECH_MODEL, 'voice': self.settings['voice'],
                    'roots': [str(r) for r in self.roots], 'show_title': self.settings['show_title'],
                    **({'public_manifest': self.public.digest} if self.public else {}),
                    'length': [self.min_words, self.target_words, self.max_words],
                    'listening': {'kind': self.kind, 'pace': self.pace, 'minutes': self.minute_bounds,
                                  'sentence_limits': listening_settings(self.settings),
                                  'speech_output_tokens': self.settings.get('speech_output_tokens', 1536),
                                  'disclosure': 'written_show_notes'}}
        identity = json.loads(json.dumps(identity))
        marker = Path(scratch) / '.openai-run.json'
        if marker.exists():
            if json.loads(marker.read_text()) != identity:
                raise OpenAIError('Resume settings differs from original OpenAI run')
        elif (resume or self.public) and any(Path(scratch).iterdir()):
            raise OpenAIError('Cannot resume unmarked legacy artifacts as an English OpenAI run')
        else:
            _atomic(marker, json.dumps(identity))
        self._ensure_no_uncertain(scratch)

    def _path(self, path, scratch):
        candidate = Path(path).expanduser()
        candidate = (Path(scratch) / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if self.public:
            self.public.verify()
            allowed = candidate in self.public.files or candidate in {p.parent for p in self.public.files} or self._public_scratch(candidate, scratch)
            if not allowed:
                raise OpenAIError('Read outside selected public files or current public artifacts')
            return candidate
        if not any(candidate.is_relative_to(root) for root in [Path(scratch).resolve(), *self.roots]):
            raise OpenAIError('Read path outside selected source scope')
        if any(part.startswith('.') for part in candidate.parts) and not candidate.is_relative_to(Path(scratch).resolve()):
            # Exchange inputs need their own explicit root; no hidden credential trees.
            if not any(candidate.is_relative_to(Path(p).expanduser().resolve()) for p in self.settings.get('additional_read_roots', [])):
                raise OpenAIError('Hidden source requires explicit additional_read_roots authorization')
        if candidate.suffix.lower() not in {'.md', '.txt', '.json', '.yaml', '.yml', '.jsonl'} and not candidate.is_dir():
            raise OpenAIError('Unsupported source file type')
        return candidate

    def _local_tool(self, name, args, scratch, plugin_root, agent):
        if agent == 'bible-distiller':
            raise OpenAIError('Bible isolation: no file/tools access')
        if name in {'read_file', 'list_files'}:
            p = self._path(args['path'], scratch)
            if agent == 'qianzhongshu' and p.name == 'character-bible.md':
                raise OpenAIError('Structured scoring is Bible-free')
            offset = args['offset']
            if type(offset) is not int or offset < 0:
                raise OpenAIError('Invalid pagination offset')
            if name == 'read_file':
                if p.stat().st_size > 2_000_000:
                    raise OpenAIError('Source exceeds 2 MB local limit')
                text = self.public.read(p) if self.public and p in self.public.files else p.read_text(encoding='utf-8')
                return text[offset:offset + 20000]
            if self.public:
                return json.dumps(sorted(str(f) for f in self.public.files if f.parent == p)[offset:offset + 100])
            paths = sorted(str(f) for f in p.rglob('*') if f.is_file()
                           and f.suffix.lower() in {'.md', '.txt', '.json', '.yaml', '.yml'}
                           and any(f.resolve().is_relative_to(r) for r in [Path(scratch).resolve(), *self.roots]))
            return json.dumps(paths[offset:offset + 100], ensure_ascii=False)
        if name == 'prep_check' and agent == 'davinci':
            if (getattr(self, 'run_date', None) and args['date'] != self.run_date) or (getattr(self, 'run_show', None) and args['show'] != self.run_show):
                raise OpenAIError('Prep date/show must match the bound run')
            if self.public:
                args = {**args, 'candidates_json': json.dumps(self.public.candidates), 'pkos_note_json': 'null'}
            for field in ['candidates_json', 'pkos_note_json']:
                json.loads(args[field])
            if args['domain'] not in {'philosophy', 'cognition', 'history'} or args['contrarian'] not in {'lesswrong', 'marginal-revolution', 'stratechery'} or args['show'] not in {'morning', 'evening'}:
                raise OpenAIError('Invalid prep route')
            dt.date.fromisoformat(args['date'])
            script = Path(plugin_root) / 'skills/podcast-studio-prep/scripts/orchestrator.py'
            argv = [sys.executable, str(script), 'check', '--candidates', args['candidates_json'],
                    '--pkos-note', args['pkos_note_json'], '--date', args['date'],
                    '--show-type', args['show'], '--force-domain', args['domain'],
                    '--force-contrarian', args['contrarian'], '--topic-log',
                    str(Path(self.config.vault.output_dir) / 'topic_log.yaml'),
                    '--vault-root', self.config.vault.root or self.config.vault.subjective_dir]
            if self.public:
                argv += ['--source-mode', 'public', '--archive-context', json.dumps(self.public.archive_context(), ensure_ascii=False)]
            if args['show'] == 'morning':
                argv += ['--required-domains', 'tech,market,science,geo,culture']
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=180, cwd=plugin_root)
            if completed.returncode:
                raise OpenAIError('Prep check failed; inspect local prep configuration')
            return completed.stdout
        raise OpenAIError('Unsupported local tool')

    def _ensure_no_uncertain(self, scratch):
        if self.public:
            self.public.verify()
        root = Path(scratch)
        if (root / '.openai-request-review.json').exists():
            raise OpenAIError('Prior API request needs outcome review; no automatic retry')
        with self._active_lock:
            if any(str(p) not in self._active_requests for p in root.glob('.openai-flight-*.json')):
                raise OpenAIError('Interrupted API request has a durable receipt; review outcome before retry')

    def _finish_request(self, flight):
        # Called only after a known response was cached and accepted/rejected,
        # or after tool results/checkpoints were durably written. Never from an
        # interrupted transport's cleanup path.
        Path(flight).unlink(missing_ok=True)
        with self._active_lock:
            self._active_requests.discard(str(flight))

    def _release_active(self, flight):
        with self._active_lock:
            self._active_requests.discard(str(flight))

    def _request(self, scratch, endpoint, payload, timeout, *, receipt=None, cost=None, purpose=None):
        self._ensure_no_uncertain(scratch)
        if receipt is None:
            receipt = self.budget.reserve([cost], purpose or endpoint)
        self.budget.verify(receipt)
        request_id = uuid.uuid4().hex
        flight = Path(scratch) / ('.openai-flight-' + request_id + '.json')
        response_file = Path(scratch) / ('.openai-response-' + request_id + '.json')
        block = Path(scratch) / '.openai-request-review.json'
        with self._active_lock:
            self._active_requests.add(str(flight))
        try:
            _atomic(flight, json.dumps({'endpoint': endpoint, 'state': 'in_flight',
                'payload_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                'reservation': receipt, 'response_file': response_file.name,
                'action': 'Review provider usage and retained receipts before any retry'}))
            def before_send():
                try:
                    self._ensure_no_uncertain(scratch)
                    self.budget.verify(receipt)
                    self._ensure_no_uncertain(scratch)  # Budget lock may have waited behind a failed sibling.
                except OpenAIError as e:
                    raise RequestNotSent(str(e)) from None
            before_send()
            kwargs = {'timeout': timeout}
            if self.transport is request_api:
                kwargs['before_send'] = before_send
            result = self.transport(endpoint, payload, **kwargs)
            if not isinstance(result, dict):
                raise OpenAIError('Invalid provider response object')
            def encode_bytes(value):
                if isinstance(value, bytes):
                    return {'base64_bytes': base64.b64encode(value).decode()}
                raise TypeError('Unserializable provider result')
            _atomic(response_file, json.dumps({'state': 'response_received', 'endpoint': endpoint,
                                              'result': result}, default=encode_bytes))
            return result, flight
        except RequestNotSent:
            # request_api only calls this verifier before the HTTP request or
            # response.create event, so this is a known unissued generation.
            self._finish_request(flight)
            raise
        except BaseException as e:
            self._release_active(flight)
            # The pre-send receipt survives even SIGKILL. This supplementary
            # marker also stops siblings from starting further paid requests.
            try:
                _atomic(block, json.dumps({'endpoint': endpoint, 'outcome': 'uncertain',
                    'receipt_file': flight.name,
                    'action': 'Review provider usage and retained reservations before retry'}))
            except OSError:
                pass
            if not isinstance(e, Exception):
                raise
            raise OpenAIError('API outcome uncertain; review required before retry; reservation retained') from None

    def __call__(self, agent_name, user_prompt, scratch_dir, expected_artifact,
                 *, step_name=None, plugin_root=None, timeout=600, agent_dir='agents',
                 whitelist=AGENT_WHITELIST):
        if agent_name not in (whitelist or AGENT_WHITELIST) or agent_dir != 'agents':
            raise DispatchError('OpenAI edition supports only whitelisted opinion-line personas')
        artifact = _resolve_artifact(scratch_dir, expected_artifact)
        if not plugin_root:
            raise DispatchError('plugin_root is required')
        flight = None
        try:
            self._ensure_no_uncertain(scratch_dir)
            if step_name == 'tts':
                self.synthesize(scratch_dir, artifact)
                return {'ok': True, 'reason': 'OpenAI speech complete', 'artifact_path': str(artifact)}
            system = (Path(plugin_root) / agent_dir / (agent_name + '.md')).read_text()
            # A fresh Responses conversation for EVERY station/candidate, never
            # previous_response_id or shared conversation state.
            tools = [] if agent_name == 'bible-distiller' else list(READ_TOOLS)
            if step_name == 'collect':
                tools.append(PREP_TOOL)
                user_prompt += '\nSelected source files: ' + json.dumps(self.public.inventory()) if self.public else '\nSelected source directories: ' + json.dumps([str(r) for r in self.roots])
            prepared = {}
            if step_name == 'collect':
                user_prompt += ('\nDo NOT include JSON fenced blocks in your answer. The host will append '
                                'the three real prep_check briefs itself. You must call prep_check for '
                                'A=philosophy/lesswrong, B=cognition/marginal-revolution, C=history/stratechery.')
            messages = [{'role': 'user', 'content': user_prompt}]
            journal_key = hashlib.sha256(json.dumps({'agent': agent_name, 'prompt': user_prompt,
                'system': system + self.contract, 'title': self.settings['show_title'], 'tools': tools,
                'artifact': expected_artifact, 'step': step_name},
                sort_keys=True).encode()).hexdigest()
            journal = Path(scratch_dir) / ('.openai-conversation-' + journal_key + '.json')
            start_turn = 0
            if journal.exists():
                saved = json.loads(journal.read_text())
                messages, prepared, start_turn = saved['messages'], saved['prepared'], saved['next_turn']
                if not isinstance(messages, list) or not isinstance(prepared, dict) or type(start_turn) is not int or not 0 <= start_turn <= 16:
                    raise OpenAIError('Invalid saved provider conversation; review before retry')
            for turn in range(start_turn, 16):
                payload = {'model': TEXT_MODEL, 'instructions': system + '\n' + self.contract + '\nEnglish show/channel name: ' + self.settings['show_title'],
                           'input': messages, 'tools': tools, 'store': False,
                           'max_output_tokens': self.max_output}
                # UTF-8 byte count upper-bounds byte-level BPE input tokens;
                # 4096 extra tokens conservatively cover API framing overhead.
                input_bound = len(json.dumps(payload, ensure_ascii=False).encode()) + 4096
                bound = (input_bound * .40 + self.max_output * 1.60) / 1_000_000
                result, flight = self._request(scratch_dir, 'responses', payload, min(timeout, 300),
                                                cost=bound, purpose=step_name or agent_name)
                if result.get('status') != 'completed':
                    raise OpenAIError('Incomplete/refused Responses result; artifact not accepted')
                output = result.get('output', [])
                calls = [item for item in output if item.get('type') == 'function_call']
                if calls:
                    messages.extend(output)
                    allowed = {t['name'] for t in tools}
                    for call in calls:
                        if call['name'] not in allowed:
                            raise OpenAIError('Model requested a disabled tool')
                        try:
                            args = json.loads(call['arguments'])
                            value = self._local_tool(call['name'], args, scratch_dir, plugin_root, agent_name)
                            if call['name'] == 'prep_check':
                                route = (args['domain'], args['contrarian'])
                                tag = {('philosophy', 'lesswrong'): 'A',
                                       ('cognition', 'marginal-revolution'): 'B',
                                       ('history', 'stratechery'): 'C'}.get(route)
                                brief = json.loads(value)
                                required = ('open_questions', 'evidence') if args.get('show') == 'evening' else ('approved_topics',)
                                if tag and isinstance(brief, dict) and not brief.get('error') and all(isinstance(brief.get(k), list) for k in required):
                                    prepared[tag] = brief
                        except (OpenAIError, OSError, ValueError, KeyError):
                            value = 'Tool refused or failed; correct your arguments or stop. No fabricated result.'
                        messages.append({'type': 'function_call_output', 'call_id': call['call_id'], 'output': value})
                    _atomic(journal, json.dumps({'messages': messages, 'prepared': prepared, 'next_turn': turn + 1}))
                    self._finish_request(flight)
                    flight = None
                    continue
                text = ''.join(c.get('text', '') for item in output if item.get('type') == 'message'
                               for c in item.get('content', []) if c.get('type') == 'output_text').strip()
                if not text:
                    raise OpenAIError('Missing artifact text')
                if step_name == 'collect':
                    if set(prepared) != {'A', 'B', 'C'} or '```json' in text:
                        raise OpenAIError('Collector must use three actual prep routes and omit model-authored JSON blocks')
                    for tag in ('A', 'B', 'C'):
                        text += '\n\nbrief-' + tag + '\n```json\n' + json.dumps(prepared[tag], ensure_ascii=False) + '\n```'
                if artifact.suffix == '.json':
                    parsed = json.loads(text)
                    if step_name == 'finalize':
                        if not isinstance(parsed, dict) or not isinstance(parsed.get('body'), str) or not self._english(parsed['body']).get('ok'):
                            raise OpenAIError('Final body failed English/word-count gate')
                        if not check_listening(parsed['body'], self.settings)['ok']:
                            raise OpenAIError('Final body failed English listening-load gate')
                        if not check_english_title(parsed.get('title')):
                            raise OpenAIError('Final title failed English-script gate')
                if step_name and (step_name.startswith(('drafts:', 'polishes:')) or step_name == 'broadcast-rewrite'):
                    if not self._english(text)['ok']:
                        raise OpenAIError('Listener prose failed English/word-count gate')
                if step_name and (step_name.startswith('polishes:') or step_name == 'broadcast-rewrite'):
                    if not check_listening(text, self.settings)['ok']:
                        raise OpenAIError('Listener prose failed sentence-load gate')
                if step_name == 'broadcast-rewrite':
                    reader = json.loads((Path(scratch_dir) / 'finalize-result.json').read_text())['body']
                    if not check_retention(reader, text)['ok']:
                        raise OpenAIError('Broadcast rewrite shortened the approved explanation; preserve substance')
                _atomic(artifact, text)
                journal.unlink(missing_ok=True)  # Only unfinished conversations are resumable.
                self._finish_request(flight)
                flight = None
                return {'ok': True, 'reason': 'OpenAI artifact landed', 'artifact_path': str(artifact)}
            raise OpenAIError('Tool turn limit exhausted')
        except (OpenAIError, OSError, ValueError, KeyError, subprocess.SubprocessError, TypeError) as e:
            if flight is not None:
                if isinstance(e, OSError):
                    # A known paid result could not be checkpointed/landed.
                    # Keep its receipt so retry cannot silently bill it again.
                    self._release_active(flight)
                else:
                    self._finish_request(flight)  # Known response rejected by local validation.
                flight = None
            # Never surface raw transport/provider messages or source content.
            return {'ok': False, 'reason': str(e) if isinstance(e, OpenAIError) else
                    f'OpenAI adapter failed ({type(e).__name__}); scratch retained', 'artifact_path': str(artifact)}
        finally:
            if flight is not None:
                self._release_active(flight)  # Preserve receipt after an interrupted accept/write.

    def synthesize(self, scratch, artifact):
        self._ensure_no_uncertain(scratch)
        if self.transport is request_api:
            import importlib.util
            if importlib.util.find_spec('websocket') is None:
                raise OpenAIError('Install the declared websocket-client dependency before any paid narration')
        if subprocess.run(['ffprobe', '-version'], capture_output=True).returncode:
            raise OpenAIError('ffprobe is required before any paid synthesis')
        if subprocess.run(['ffmpeg', '-version'], capture_output=True).returncode:
            raise OpenAIError('ffmpeg is required before any paid synthesis')
        scripts = list(Path(scratch).glob('broadcast-script-*.txt'))
        if len(scripts) != 1:
            raise OpenAIError('Expected exactly one broadcast script')
        text = scripts[0].read_text()
        if not self._english(narration_body(text))['ok']:
            raise OpenAIError('Speech input failed English/word-count gate')
        if not check_listening(text, self.settings)['ok']:
            raise OpenAIError('Speech input failed English listening-load gate')
        final = Path(scratch) / 'finalize-result.json'
        if final.exists():
            reader = json.loads(final.read_text())['body']
            if not check_retention(reader, text)['ok']:
                raise OpenAIError('Speech input lost approved manuscript detail')
        elif self.kind == 'full':
            raise OpenAIError('Full narration requires the approved finalized manuscript')
        if self.kind == 'full':
            verdict_path = Path(scratch) / 'scorecard-verdict.json'
            try:
                verdict = json.loads(verdict_path.read_text())
            except (OSError, ValueError):
                raise OpenAIError('Full narration requires the independent listening/meaning review') from None
            pins = {'reader': hashlib.sha256(reader.encode()).hexdigest(),
                    'spoken': hashlib.sha256(text.encode()).hexdigest()}
            if (verdict.get('english_review_artifact_hashes') != pins
                    or not validate_listening_review(reader, text, verdict.get('english_listening_review'))['ok']):
                raise OpenAIError('Full narration meaning review is missing, negative or stale')
        # Never mutate or prepend spoken text. Keep mandatory disclosure visible
        # in written show notes, carried beside the audio by the existing publisher.
        land_once(Path(scratch) / 'audio-disclosure.md',
                  'Voice disclosure: This episode uses an AI-generated voice, not a human narrator.\n'
                  'Display this notice with the audio in the player description or show notes.\n')
        words, chunks, current = text.split(), [], []
        for word in words:
            if len(word) > 1600:
                raise OpenAIError('Narration word exceeds local chunk bound')
            if len(current) >= 80 or len(' '.join(current + [word])) > 1800:
                chunks.append(' '.join(current))
                current = []
            current.append(word)
        if current:
            chunks.append(' '.join(current))
        max_tokens = self.settings.get('speech_output_tokens', 1536)
        payloads = [speech_payload(c, self.settings['voice'], max_tokens, speaking_rate_wpm=self.pace) for c in chunks]
        job = hashlib.sha256(json.dumps({'model': SPEECH_MODEL, 'requests': payloads}).encode()).hexdigest()
        folder = Path(scratch) / ('.openai-speech-' + job[:16])
        folder.mkdir(exist_ok=True)
        pending = []
        for i, payload in enumerate(payloads):
            segment = folder / f'{i:04}.pcm'
            if segment.exists():
                if not segment.stat().st_size or segment.stat().st_size % 2:
                    raise OpenAIError('Cached PCM is invalid; review before any repeat request')
            else:
                pending.append((i, payload))
        receipt = self.budget.reserve([cost_bound(p) for _, p in pending], 'narration:' + job) if pending else None
        for i, payload in pending:
            self.budget.verify(receipt)
            result, flight = self._request(scratch, 'realtime', payload, 120, receipt=receipt)
            try:
                pcm, transcript = result.get('pcm'), result.get('transcript', '')
                expected = chunks[i]
                if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or narration_words(transcript) != narration_words(expected):
                    raise OpenAIError('Narration audio/transcript does not match the final script; chunk not accepted')
                _atomic(folder / f'{i:04}.pcm', pcm)
            except OSError:
                self._release_active(flight)  # Retain known paid result until checkpoint recovery is reviewed.
                raise
            except Exception:
                self._finish_request(flight)  # Complete provider result rejected by local validation.
                raise
            except BaseException:
                self._release_active(flight)
                raise
            else:
                self._finish_request(flight)
        combined = folder / 'combined.pcm'
        _atomic(combined, b''.join((folder / f'{i:04}.pcm').read_bytes() for i in range(len(chunks))))
        merged = folder / 'merged.mp3'
        done = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ar', '24000', '-ac', '1',
                               '-i', str(combined), '-codec:a', 'libmp3lame', str(merged)], capture_output=True)
        if done.returncode or not merged.is_file() or not merged.stat().st_size:
            raise OpenAIError('Narration encoding failed; completed chunks retained')
        valid = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(merged), '-f', 'null', '-'], capture_output=True)
        if valid.returncode:
            raise OpenAIError('Narration decode validation failed; no final audio landed')
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'json', str(merged)], capture_output=True, text=True)
        try:
            seconds = float(json.loads(probe.stdout)['format']['duration'])
        except (ValueError, KeyError, TypeError):
            raise OpenAIError('Narration duration could not be measured; completed chunks retained') from None
        duration = check_audio_duration(seconds, self.settings)
        _atomic(Path(scratch) / 'audio-duration.json', json.dumps(duration))
        if probe.returncode or not duration['ok']:
            raise OpenAIError('Narration outside configured full-episode duration; completed chunks retained for review')
        _atomic(artifact, merged.read_bytes())


def settings_from_config(config):
    """Use the ordinary config/runner; never replace output or memory paths."""
    production = getattr(config, 'production', None)
    if production is None or production.provider != 'openai':
        return None
    identity = getattr(config, 'podcast', None)
    if identity is None or not identity.name:
        raise OpenAIError('podcast.name is required in the existing project config')
    settings = {'model': TEXT_MODEL, 'speech_model': SPEECH_MODEL, 'voice': 'marin',
                **(production.options or {}),
                'show_title': identity.english_name or identity.name}
    if 'english_output_dir' in settings:
        raise OpenAIError('Use existing vault.output_dir; no English output directory is supported')
    return settings
