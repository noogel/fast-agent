import json
import pytest
from fast_agent.models import ToolCall
from fast_agent.tools import ToolContext, build_default_registry


def setup(tmp_path, confirm=None):
    path = tmp_path / 'sample.py'
    path.write_text('alpha\nbeta\n')
    previews = []
    context = ToolContext(tmp_path, confirm or (lambda prompt: previews.append(prompt) or True), True)
    registry = build_default_registry()
    registry.execute(ToolCall('read', 'read_file', '{"path":"sample.py"}'), context)
    return path, context, registry, previews


def edit(registry, context, edits):
    return registry.execute(ToolCall('edit', 'multi_edit_file', json.dumps({'path': 'sample.py', 'edits': edits})), context)


def test_batch_commits_once_and_supports_sequential_replacements(tmp_path):
    path, context, registry, previews = setup(tmp_path)
    result = edit(registry, context, [{'old': 'alpha', 'new': 'gamma'}, {'old': 'gamma\nbeta', 'new': 'delta\n中文'}])
    assert result.ok
    assert path.read_text() == 'delta\n中文\n'
    assert len(previews) == 1
    assert '-alpha' in previews[0] and '+中文' in previews[0]
    assert edit(registry, context, [{'old': 'delta', 'new': 'epsilon'}]).ok


@pytest.mark.parametrize('edits', [[], [{'old': 'x', 'new': 'y'}] * 101,
    [{'old': '', 'new': 'x'}], [{'old': 'alpha', 'new': 'beta'}, {'old': 'beta', 'new': 'z'}],
    [{'old': 'alpha', 'new': 'z'}, {'old': 'missing', 'new': 'q'}],
    [{'old': 'alpha'}], [{'old': 'alpha', 'new': 42}]])
def test_invalid_batch_never_writes_or_confirms(tmp_path, edits):
    path, context, registry, previews = setup(tmp_path)
    assert 'multi_edit_file' in registry.tools
    result = edit(registry, context, edits)
    assert not result.ok
    assert path.read_text() == 'alpha\nbeta\n'
    assert previews == []


@pytest.mark.parametrize('boundary', ['unread', 'external', 'rules', 'denied', 'plan', 'confirmation_race'])
def test_existing_edit_boundaries_apply_to_batch(tmp_path, boundary):
    path, context, registry, previews = setup(tmp_path)
    assert 'multi_edit_file' in registry.tools
    expected = path.read_text()
    if boundary == 'unread':
        context.read_versions.clear()
    elif boundary == 'external':
        path.write_text('external\n'); expected = 'external\n'
    elif boundary == 'rules':
        (tmp_path / 'AGENTS.md').write_text('New instructions')
    elif boundary == 'denied':
        context.confirm = lambda _: False
    elif boundary == 'plan':
        context.mode = 'plan'
        assert 'multi_edit_file' not in [s['function']['name'] for s in registry.schemas(read_only=True)]
    else:
        def race(_):
            path.write_text('external\n')
            return True
        context.confirm = race
        expected = 'external\n'
    assert not edit(registry, context, [{'old': 'alpha', 'new': 'gamma'}]).ok
    assert path.read_text() == expected


def test_batch_noop_needs_no_confirmation(tmp_path):
    path, context, registry, previews = setup(tmp_path)
    result = edit(registry, context, [{'old': 'alpha', 'new': 'alpha'}])
    assert result.ok and result.data['changed'] is False
    assert not previews


def test_batch_content_limit_rejects_before_confirmation(tmp_path):
    from fast_agent.file_tools import FILE_LIMIT
    path, context, registry, previews = setup(tmp_path)
    result = edit(registry, context, [{'old': 'alpha', 'new': 'x' * FILE_LIMIT}])
    assert not result.ok and '1 MiB' in result.error
    assert path.read_text() == 'alpha\nbeta\n' and not previews


def test_batch_accepts_maximum_count(tmp_path):
    path, context, registry, previews = setup(tmp_path)
    edits = [{'old': f'word{i}', 'new': f'word{i+1}'} for i in range(100)]
    path.write_text('word0\n')
    registry.execute(ToolCall('read', 'read_file', '{"path":"sample.py"}'), context)
    assert edit(registry, context, edits).ok
    assert path.read_text() == 'word100\n' and len(previews) == 1


@pytest.mark.parametrize('tool', ['edit_file', 'multi_edit_file'])
def test_overlapping_matches_are_ambiguous(tmp_path, tool):
    path, context, registry, previews = setup(tmp_path)
    path.write_text('aaaa')
    registry.execute(ToolCall('read', 'read_file', '{"path":"sample.py"}'), context)
    args = {'path': 'sample.py', 'old': 'aaa', 'new': 'X'}
    if tool == 'multi_edit_file':
        args = {'path': 'sample.py', 'edits': [{'old': 'aaa', 'new': 'X'}]}
    result = registry.execute(ToolCall('edit', tool, json.dumps(args)), context)
    assert not result.ok
    assert path.read_text() == 'aaaa' and not previews
