import json
import pytest
from fast_agent.models import ToolCall
from fast_agent.tools import ToolContext, build_default_registry


def search(root, **args):
    return build_default_registry().execute(ToolCall('s','search_code',json.dumps(args)),ToolContext(root,lambda _:False,False,mode='plan'))


def test_glob_preserves_ignore_and_context(tmp_path):
    (tmp_path/'src/nested').mkdir(parents=True)
    (tmp_path/'src/a.py').write_text('before\ndef add(a, b):\n    return a-b\n')
    (tmp_path/'src/nested/b.py').write_text('def add(x,y): pass\n')
    (tmp_path/'src/a.txt').write_text('def add(x,y): pass')
    (tmp_path/'src/ignored.py').write_text('def add(x,y): pass')
    (tmp_path/'.gitignore').write_text('ignored.py\n')
    (tmp_path/'.env.py').write_text('def add(secret): pass')
    result=search(tmp_path, query=r'^def add\(', regex=True, glob='src/**/*.py', context_lines=1)
    assert result.ok
    rows=result.data['matches']
    assert {r['path'] for r in rows}=={'src/a.py','src/nested/b.py'}
    row=next(r for r in rows if r['path']=='src/a.py')
    assert row['line']==2
    assert row['before']==[{'line':1,'text':'before\n'}]
    assert row['after']==[{'line':3,'text':'    return a-b\n'}]


def test_literal_default_and_invalid_regex(tmp_path):
    (tmp_path/'a').write_text('a.b\naxb\n')
    assert len(search(tmp_path,query='a.b').data['matches'])==1
    assert len(search(tmp_path,query='a.b',regex=True).data['matches'])==2
    assert not search(tmp_path,query='[',regex=True).ok


@pytest.mark.parametrize('extra',[{'context_lines':-1},{'context_lines':6},{'glob':'../*'},{'glob':'/tmp/*'},{'glob':''},{'regex':'true'}])
def test_invalid_options(tmp_path,extra):
    assert not search(tmp_path,query='x',**extra).ok


def test_line_and_result_limits_are_explicit(tmp_path):
    (tmp_path/'a.py').write_text('x'*700+'\nx\n')
    result=search(tmp_path,query='x',limit=1,context_lines=1)
    assert result.ok and result.data['truncated']
    assert len(result.data['matches'])==1
    assert result.data['matches'][0]['text_truncated']


def test_single_star_does_not_cross_directory(tmp_path):
    (tmp_path/'src/nested').mkdir(parents=True)
    (tmp_path/'src/a.py').write_text('target')
    (tmp_path/'src/nested/b.py').write_text('target')
    result=search(tmp_path,query='target',path='src',glob='src/*.py')
    assert [r['path'] for r in result.data['matches']]==['src/a.py']


def test_raw_truncation_remains_visible_after_glob_filter(tmp_path):
    (tmp_path/'noise.txt').write_text(('target '+'x'*1000+'\n')*100)
    result=search(tmp_path,query='target',glob='**/*.py')
    assert result.ok and result.data['matches']==[] and result.data['truncated']


def test_context_expansion_has_total_budget(tmp_path):
    (tmp_path/'a.py').write_text(('target '+'x'*390+'\n')*60)
    result=search(tmp_path,query='target',context_lines=5)
    assert result.ok and result.data['truncated']
    assert 0 < len(result.data['matches']) < 60
    assert len(json.dumps(result.data,ensure_ascii=False).encode())<=64*1024
