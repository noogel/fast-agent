import json,shutil
import pytest
from fast_agent.tools import ToolContext,ToolRegistry
from fast_agent.models import ToolCall
from fast_agent.lsp_tools import lsp_tools


def setup_tools(tmp_path,confirm=lambda _:True):
    executable=shutil.which('clangd')
    if not executable:pytest.skip('需要 clangd')
    config=tmp_path/'lsp.json'
    config.write_text(json.dumps({'argv':[executable,'--background-index=0'],'language_id':'cpp','extensions':['.cpp'],'timeout':10}))
    registry=ToolRegistry()
    for tool in lsp_tools(config):registry.register(tool)
    return registry,ToolContext(tmp_path,confirm,True)


def test_real_tools(tmp_path):
    registry,context=setup_tools(tmp_path)
    (tmp_path/'main.cpp').write_text('int answer(){return 42;}\nint main(){return answer();}\n')
    result=registry.execute(ToolCall('1','lsp_symbols','{"path":"main.cpp"}'),context)
    assert result.ok and {'answer','main'} <= {item['name'] for item in result.data['result']}
    result=registry.execute(ToolCall('2','lsp_definition','{"path":"main.cpp","line":2,"column":19}'),context)
    assert result.ok and result.data['result'][0]['uri'].endswith('/main.cpp')
    (tmp_path/'main.cpp').write_text('int main(){return unknown_name;}\n')
    result=registry.execute(ToolCall('3','lsp_diagnostics','{"path":"main.cpp"}'),context)
    assert result.ok and any('unknown_name' in item['message'] for item in result.data['result']['items'])
    assert all(item['effect']=='lsp_start' for item in context.audit)


def test_refusal_and_path_escape(tmp_path):
    registry,context=setup_tools(tmp_path,lambda _:False)
    (tmp_path/'main.cpp').write_text('int main(){}')
    assert not registry.execute(ToolCall('1','lsp_symbols','{"path":"main.cpp"}'),context).ok
    assert not registry.execute(ToolCall('2','lsp_symbols','{"path":"../outside.cpp"}'),context).ok
    context.mode='plan'
    assert not registry.execute(ToolCall('3','lsp_symbols','{"path":"main.cpp"}'),context).ok


def test_config_rejects_unknown_fields(tmp_path):
    config=tmp_path/'lsp.json';config.write_text('{"argv":["clangd"],"language_id":"cpp","extensions":[".cpp"],"secret":"bad"}')
    with pytest.raises(ValueError):lsp_tools(config)
