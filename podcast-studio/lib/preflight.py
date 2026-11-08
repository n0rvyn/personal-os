"""Read-only readiness audit for the existing runner; never invokes a provider."""
from __future__ import annotations
import datetime as dt
import re
import os
from zoneinfo import ZoneInfo
from lib.config import _read_yaml, _resolve_config_path
from lib.openai_runtime import Budget
from lib.public_sources import PublicSources


def inspect_readiness(*, show, run_at=None, max_news_age_days=7, policy_timezone=None):
    """Audit the real config without load_config's layout writes or reservations.

    A passing result validates declared metadata, not independent account-wide
    billing coverage, original-source accuracy, or an entire episode cost quote.
    Recheck at execution time; today's report is never tomorrow's authorization.
    """
    if type(max_news_age_days) is not int or max_news_age_days < 0:
        raise ValueError('max_news_age_days must be a nonnegative integer')
    at = dt.datetime.fromisoformat(run_at) if run_at else dt.datetime.now(dt.timezone.utc)
    if at.tzinfo is None:
        raise ValueError('preflight run time must include its UTC offset')
    config_path = _resolve_config_path(None)
    raw = _read_yaml(config_path)
    production = raw.get('production', {})
    if not isinstance(production, dict):
        raise ValueError('production configuration must be a mapping')
    checks = []
    def record(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    policy_at = at
    try:
        zone_name = policy_timezone or production.get('budget_timezone')
        if not isinstance(zone_name, str) or not zone_name:
            raise ValueError('Explicit policy timezone or production.budget_timezone is required')
        policy_at = at.astimezone(ZoneInfo(zone_name))
        record('policy_timezone', True, zone_name)
    except (ValueError, KeyError) as exc:
        record('policy_timezone', False, str(exc))
    record('backend', production.get('provider') == 'openai' and production.get('language') == 'en'
           and show in {'morning', 'evening'}, 'Existing OpenAI English morning/evening runner required')
    record('credential_environment', bool(os.environ.get('OPENAI_API_KEY')),
           'Presence only; no key is read from a file, printed, persisted or validated with OpenAI')
    record('source_authorization', (production.get('source_authorized') is True or production.get('source_authorized') == 'true'),
           'Selected-source egress authorization must be present for this run')
    source_report = None
    try:
        if production.get('source_mode') != 'public':
            raise ValueError('Scheduled public-source trial requires source_mode: public')
        pin = production.get('source_manifest_sha256')
        if not isinstance(pin, str) or not re.fullmatch(r'[0-9a-f]{64}', pin):
            raise ValueError('Approved source_manifest_sha256 must be an explicit 64-character lowercase hex pin')
        sources = PublicSources(production['source_manifest'], pin)
        record('source_integrity', True, 'Manifest and every selected file hash verified locally')
        source_report = {'manifest': str(sources.path), 'sha256': sources.digest,
                         'inventory': sources.inventory()}
        try:
            value = sources.data.get('reviewed_at')
            if not isinstance(value, str) or not value:
                raise ValueError('Manifest lacks reviewed_at: verify original sources and record the actual review time')
            reviewed = dt.datetime.fromisoformat(value)
            if reviewed.tzinfo is None:
                raise ValueError('Manifest reviewed_at must include UTC offset')
            review_age = (at - reviewed).total_seconds() / 3600
            record('source_review_age', 0 <= review_age <= 24,
                   {'hours_at_run': review_age, 'maximum_hours': 24,
                    'meaning': 'Declared original-source review time; not independent proof of review'})
        except ValueError as exc:
            record('source_review_age', False, str(exc))
        news = [x for x in sources.inventory() if x['kind'] == 'news']
        stale = [x for x in news if not 0 <= (policy_at.date() - dt.date.fromisoformat(x['published'])).days <= max_news_age_days]
        record('news_publication_window', not stale,
               {'maximum_days': max_news_age_days, 'outside_window': stale,
                'note': 'Archive context may be older; capture dates do not replace publication dates'})
        source_report = {'manifest': str(sources.path), 'sha256': sources.digest,
                         'inventory': sources.inventory()}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record('source_preflight', False, str(exc))
    budget_report = None
    try:
        missing = [k for k in ('budget_timezone', 'budget_ledger') if not production.get(k)]
        if missing:
            raise ValueError('Missing production configuration: ' + ', '.join(missing))
        timezone = production['budget_timezone']
        if policy_timezone is not None and timezone != policy_timezone:
            raise ValueError('Configured budget timezone differs from the expected trial policy timezone')
        day = at.astimezone(ZoneInfo(timezone)).date().isoformat()
        settings = {'budget_timezone': timezone, 'budget_ledger': production['budget_ledger'],
                    'per_call_limit_usd': float(production['per_call_limit_usd']),
                    'daily_limit_usd': float(production['daily_limit_usd'])}
        if settings['per_call_limit_usd'] > .10 or settings['daily_limit_usd'] > 2:
            raise ValueError('Standing $0.10/call and $2/day limits must not be weakened')
        budget_report = Budget(settings).snapshot(day=day)
        record('shared_budget', budget_report['remaining_usd'] > 0, budget_report)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record('shared_budget', False, 'Existing reconciled shared ledger required: ' + str(exc))
    return {'ready_for_run': all(x['ok'] for x in checks), 'config': str(config_path),
            'show': show, 'run_at': at.isoformat(), 'checks': checks,
            'sources': source_report, 'budget': budget_report,
            'paid_calls': 0, 'reservations_created': 0, 'execution_recheck_required': True,
            'limitations': ['Recheck original sources and shared commitments at execution time.',
                'This audit never grants source, credential or spending authority.',
                'A positive remaining balance is not proof that a full episode will fit; every request remains guarded.',
                'Publication and daily recurrence are outside the one-episode trial.']}
