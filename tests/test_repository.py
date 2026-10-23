import json
import subprocess
from pathlib import Path
import pytest
from fast_agent.models import ToolCall
from fast_agent.tools import ToolContext, build_default_registry


def call(ctx,name,**args):
    return build_default_registry().execute(ToolCall('test',name,json.dumps(args)),ctx)


def test_discovery_and_rules(tmp_path):
    (tmp_path/'AGENTS.md').write_text('使用 pytest')
    (tmp_path/'src').mkdir(); (tmp_path/'src/AGENTS.md').write_text('保留接口')
    (tmp_path/'src/a.py').write_text('alpha\nbeta\ngamma\n')
    (tmp_path/'.env').write_text('SECRET=private')
    (tmp_path/'.gitignore').write_text('ignored.py\n')
    (tmp_path/'ignored.py').write_text('alpha')
    ctx=ToolContext(tmp_path,lambda _:True,True)
    listing=call(ctx,'list_files',limit=2)
    assert listing.ok and len(listing.data['files'])==2 and listing.data['truncated']
    search=call(ctx,'search_code',query='beta')
    assert search.ok and search.data['matches'][0]['line']==2
    assert not call(ctx,'search_code',query='SECRET').data['matches']
    read=call(ctx,'read_file',path='src/a.py',start_line=2,end_line=2)
    assert read.ok and read.data['content']=='beta\n'
    assert [i['content'] for i in read.data['instructions']]==['使用 pytest','保留接口']


def test_preview_and_external_change(tmp_path):
    file=tmp_path/'a.py';file.write_text('old\n')
    previews=[]
    ctx=ToolContext(tmp_path,lambda prompt:previews.append(prompt) or True,True)
    assert not call(ctx,'edit_file',path='a.py',old='old',new='new').ok
    assert call(ctx,'read_file',path='a.py').ok
    file.write_text('external\n')
    result=call(ctx,'edit_file',path='a.py',old='external',new='new')
    assert not result.ok and '变化' in result.error and not previews
    assert call(ctx,'read_file',path='a.py').ok
    assert call(ctx,'edit_file',path='a.py',old='external',new='new').ok
    assert '-external' in previews[-1] and '+new' in previews[-1]


def test_change_during_confirmation(tmp_path):
    file=tmp_path/'a';file.write_text('old')
    def confirm(_): file.write_text('external');return True
    ctx=ToolContext(tmp_path,confirm,True)
    call(ctx,'read_file',path='a')
    assert not call(ctx,'edit_file',path='a',old='old',new='new').ok
    assert file.read_text()=='external'


def test_rule_change_requires_reread(tmp_path):
    (tmp_path/'a').write_text('old');rules=tmp_path/'AGENTS.md';rules.write_text('rules')
    ctx=ToolContext(tmp_path,lambda _:True,True)
    call(ctx,'read_file',path='a');rules.write_text('changed')
    assert not call(ctx,'edit_file',path='a',old='old',new='new').ok


def test_denied_new_write(tmp_path):
    ctx=ToolContext(tmp_path,lambda _:False,True)
    assert not call(ctx,'write_file',path='new',content='hello').ok
    assert not (tmp_path/'new').exists()


def test_git_readonly(tmp_path):
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    (tmp_path/'a').write_text('old\n')
    subprocess.run(['git','-C',str(tmp_path),'add','a'],check=True)
    (tmp_path/'a').write_text('new\n')
    ctx=ToolContext(tmp_path,lambda _:False,False)
    status=call(ctx,'git_status');diff=call(ctx,'git_diff')
    assert status.ok and 'a' in status.data['output']
    assert diff.ok and '-old' in diff.data['output'] and '+new' in diff.data['output']
    assert not call(ctx,'git_diff',path='../outside').ok


def test_read_ranges_and_limits(tmp_path):
    (tmp_path/'big').write_text('x'*200+'\n'+'target\n'+'x'*200+'\n')
    ctx=ToolContext(tmp_path,lambda _:False,False)
    assert call(ctx,'read_file',path='big',start_line=2,end_line=2).data['content']=='target\n'
    assert not call(ctx,'read_file',path='big',start_line=0).ok
    assert not call(ctx,'list_files',limit=10001).ok
    assert not call(ctx,'search_code',query='').ok

def test_empty_file_and_confirm_mode(tmp_path):
    file=tmp_path/'empty';file.write_text('')
    ctx=ToolContext(tmp_path,lambda _:True,True)
    assert call(ctx,'read_file',path='empty').ok
    file.chmod(0o755)
    assert call(ctx,'write_file',path='empty',content='new\n').ok
    assert file.stat().st_mode & 0o777 == 0o755


def test_root_rules_in_model_context(tmp_path):
    from fast_agent.agent import Agent
    from fast_agent.models import ModelReply
    (tmp_path/'AGENTS.md').write_text('工程规则：不要修改测试')
    class Model:
        def complete(self,messages,tools):
            assert '工程规则：不要修改测试' in messages[0].content
            return ModelReply('收到')
    agent=Agent(Model(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    assert agent.run('hello').completed

def test_symlink_rules_refused(tmp_path):
    (tmp_path/'rules').write_text('x')
    (tmp_path/'AGENTS.md').symlink_to(tmp_path/'rules')
    (tmp_path/'a').write_text('a')
    assert not call(ToolContext(tmp_path,lambda _:False,False),'read_file',path='a').ok


def test_new_file_inherits_rules(tmp_path):
    (tmp_path/'AGENTS.md').write_text('规则')
    ctx=ToolContext(tmp_path,lambda _:True,True)
    assert not call(ctx,'write_file',path='new.py',content='new').ok
    assert call(ctx,'read_instructions',path='new.py').ok
    assert call(ctx,'write_file',path='new.py',content='new').ok


def test_search_and_file_limits(tmp_path):
    (tmp_path/'a').write_text('hit\n'*10)
    ctx=ToolContext(tmp_path,lambda _:False,False)
    result=call(ctx,'search_code',query='hit',limit=2)
    assert result.ok and len(result.data['matches'])==2 and result.data['truncated']
    (tmp_path/'link').symlink_to(tmp_path.parent,target_is_directory=True)
    assert not call(ctx,'list_files',path='link').ok


def test_atomic_failure_keeps_source(tmp_path,monkeypatch):
    import os
    source=tmp_path/'a';source.write_text('old')
    ctx=ToolContext(tmp_path,lambda _:True,True)
    call(ctx,'read_file',path='a')
    original_replace = os.replace
    def fail(src, dst):
        if dst == source:
            raise OSError('replace failure')
        return original_replace(src, dst)
    monkeypatch.setattr(os,'replace',fail)
    assert not call(ctx,'edit_file',path='a',old='old',new='new').ok
    assert source.read_text() == 'old'
    assert not list(tmp_path.glob('.fast-agent-edit-*'))
    from fast_agent.edit_history import EditHistory
    assert EditHistory(tmp_path).list()[0]['status'] == 'pending'

@pytest.mark.parametrize('tool',['git_status','git_diff'])
def test_git_disables_clean_filters(tmp_path,tool):
    def git(*args): subprocess.run(['git','-C',str(tmp_path),*args],check=True,capture_output=True)
    git('init','-q')
    (tmp_path/'a.txt').write_text('old\n')
    git('add','a.txt')
    (tmp_path/'.gitattributes').write_text('*.txt filter=evil\n')
    git('config','filter.evil.clean','touch marker; cat')
    git('config','filter.evil.required','true')
    (tmp_path/'a.txt').write_text('changed content\n')
    result=call(ToolContext(tmp_path,lambda _:False,False),tool)
    assert result.ok
    assert not (tmp_path/'marker').exists()


def test_ripgrep_ignores_user_config(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir()
    outside=tmp_path/'outside';outside.write_text('outside-secret\n')
    (root/'link').symlink_to(outside)
    config=tmp_path/'rgconfig';config.write_text('--follow\n')
    monkeypatch.setenv('RIPGREP_CONFIG_PATH',str(config))
    result=call(ToolContext(root,lambda _:False,False),'search_code',query='outside-secret')
    assert result.ok and not result.data['matches']

def test_git_disables_process_filter(tmp_path):
    def git(*args): subprocess.run(['git','-C',str(tmp_path),*args],check=True,capture_output=True)
    git('init','-q');(tmp_path/'a.txt').write_text('old\n');git('add','a.txt')
    (tmp_path/'.gitattributes').write_text('*.txt filter=evil\n')
    git('config','filter.evil.process','touch process-marker; exit 1')
    git('config','filter.evil.required','true')
    (tmp_path/'a.txt').write_text('new content\n')
    assert call(ToolContext(tmp_path,lambda _:False,False),'git_diff').ok
    assert not (tmp_path/'process-marker').exists()


def test_listing_preserves_newline_filename(tmp_path):
    name='line\nbreak.py';(tmp_path/name).write_text('hello')
    result=call(ToolContext(tmp_path,lambda _:False,False),'list_files')
    assert result.ok and result.data['files']==[name]

def test_diff_preview_no_newline_and_controls(tmp_path):
    file=tmp_path/'a';file.write_text('old\x1b[31m')
    previews=[];ctx=ToolContext(tmp_path,lambda p:previews.append(p) or False,True)
    call(ctx,'read_file',path='a')
    result=call(ctx,'edit_file',path='a',old='old\x1b[31m',new='new')
    assert not result.ok
    assert '\x1b' not in previews[0] and '\\x1b' in previews[0]
    assert '\\ No newline at end of file' in previews[0]
