"""The supported dev loader uses canonical code and existing project config."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_project_launcher_resolves_source_and_config_without_copy(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    config = project / 'config.yaml'
    config.write_text('vault: existing')
    entry = project / 'studio-dev'
    entry.symlink_to(ROOT / 'tools/claude-dev.sh')
    fake = tmp_path / 'fake-claude'
    fake.write_text('#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({"args":sys.argv[1:],"config":os.environ.get("PODCAST_STUDIO_CONFIG"),"cwd":os.getcwd()}))\n')
    fake.chmod(0o755)
    env = {**os.environ, 'CLAUDE_BIN': str(fake)}
    env.pop('PODCAST_STUDIO_CONFIG', None)
    result = subprocess.run([str(entry), 'plugin', 'list', '--json'], env=env,
                            cwd=tmp_path, text=True, capture_output=True, check=True)
    data = json.loads(result.stdout)
    assert data['args'] == ['--plugin-dir', str(ROOT), 'plugin', 'list', '--json']
    assert data['config'] == str(config)
    assert data['cwd'] == str(project)
    assert entry.is_symlink()
    env['PODCAST_STUDIO_CONFIG'] = str(tmp_path / 'explicit.yaml')
    result = subprocess.run([str(entry), '--help'], env=env,
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)['config'] == env['PODCAST_STUDIO_CONFIG']
