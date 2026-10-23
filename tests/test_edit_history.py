import pytest
from fast_agent.file_tools import read_file, edit_file
from fast_agent.tools import ToolContext


def edited(root):
    path = root / 'a.txt'
    path.write_text('before\n')
    ctx = ToolContext(root, lambda _: True, True)
    read_file({'path': 'a.txt'}, ctx)
    result = edit_file({'path': 'a.txt', 'old': 'before', 'new': 'after'}, ctx)
    assert 'edit_id' in result.data
    return path, ctx, result.data['edit_id']


def test_persistent_undo(tmp_path):
    path, ctx, ident = edited(tmp_path)
    from fast_agent.edit_history import EditHistory
    history = EditHistory(tmp_path)
    assert history.list()[0]['id'] == ident
    fresh = ToolContext(tmp_path, lambda _: True, True)
    assert history.undo(ident, fresh).ok
    assert path.read_text() == 'before\n'
    with pytest.raises(ValueError):
        history.undo(ident, fresh)


@pytest.mark.parametrize('case', ['external', 'plan', 'denied', 'race'])
def test_undo_protection(tmp_path, case):
    path, ctx, ident = edited(tmp_path)
    from fast_agent.edit_history import EditHistory
    if case == 'external':
        path.write_text('external')
    elif case == 'plan':
        ctx.mode = 'plan'
    elif case == 'denied':
        ctx.confirm = lambda _: False
    else:
        def race(_):
            path.write_text('external')
            return True
        ctx.confirm = race
    try:
        result = EditHistory(tmp_path).undo(ident, ctx)
        assert not result.ok
    except ValueError:
        pass
    assert path.read_text() == ('external' if case in {'external', 'race'} else 'after\n')


def test_new_file_and_pending_cannot_undo(tmp_path):
    from fast_agent.file_tools import write_file
    from fast_agent.edit_history import EditHistory
    ctx = ToolContext(tmp_path, lambda _: True, True)
    result = write_file({'path': 'new.txt', 'content': 'new'}, ctx)
    history = EditHistory(tmp_path)
    with pytest.raises(ValueError):
        history.undo(result.data['edit_id'], ctx)
    path, ctx, ident = edited(tmp_path)
    history.finish(history.load(ident), 'pending')
    with pytest.raises(ValueError):
        history.undo(ident, ctx)
    assert path.read_text() == 'after\n'


def test_local_commands(tmp_path, capsys):
    from types import SimpleNamespace
    from fast_agent.session_commands import handle_session_command
    path, ctx, ident = edited(tmp_path)
    agent = SimpleNamespace(context=ctx)
    handle_session_command('/changes', agent)
    assert ident in capsys.readouterr().out
    handle_session_command('/undo ' + ident, agent)
    assert path.read_text() == 'before\n'


def test_startup_readonly_denies_undo(tmp_path):
    from fast_agent.permissions import PermissionPolicy
    from fast_agent.edit_history import EditHistory
    path, ctx, ident = edited(tmp_path)
    ctx.policy = PermissionPolicy(mode='read-only')
    assert not EditHistory(tmp_path).undo(ident, ctx).ok
    assert path.read_text() == 'after\n'


def test_failed_receipt_after_undo_stays_unretryable(tmp_path, monkeypatch):
    from fast_agent.edit_history import EditHistory
    from fast_agent.runtime_store import RuntimeStore
    path, ctx, ident = edited(tmp_path)
    original = RuntimeStore.write
    def fail(self, name, data):
        if data.get('status') == 'undone':
            raise OSError('receipt failed')
        return original(self, name, data)
    monkeypatch.setattr(RuntimeStore, 'write', fail)
    with pytest.raises(OSError):
        EditHistory(tmp_path).undo(ident, ctx)
    assert path.read_text() == 'before\n'
    assert EditHistory(tmp_path).load(ident)['status'] == 'undoing'
    with pytest.raises(ValueError):
        EditHistory(tmp_path).undo(ident, ToolContext(tmp_path, lambda _: True, True))


def test_restart_undo_checks_persisted_uncertain_tasks(tmp_path):
    from fast_agent.tasks import TaskStore
    from fast_agent.edit_history import EditHistory
    from fast_agent.models import Message, ToolCall
    path, ctx, ident = edited(tmp_path)
    call = ToolCall('uncertain', 'run_command', '{}')
    TaskStore(tmp_path).save('other-task', [Message('system', 'system'), Message('user', 'task'),
        Message('assistant', '', tool_calls=[call])], [call], 'uncertain', 'interrupted', [])
    fresh = ToolContext(tmp_path, lambda _: True, True)
    assert not EditHistory(tmp_path).undo(ident, fresh).ok
    assert path.read_text() == 'after\n'
