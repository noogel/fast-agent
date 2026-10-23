import pytest
from fast_agent.skill_metadata import metadata


def test_plain_and_folded_description():
    assert metadata('正文','review')=={'name':'review','description':''}
    result=metadata('---\nname: review\ndescription: >\n  检查代码\n  与测试\n---\n正文','review')
    assert result['description']=='检查代码 与测试'


@pytest.mark.parametrize('header',[
    'name: other\ndescription: text',
    'name: review\nname: review',
    'description: [one, two]',
    'description: &a text',
    'description: !!python/object:os.system {}',
    'allowed-tools: run_command',
    'description: true',
    'description: '+('x'*1025),
])
def test_invalid_metadata(header):
    with pytest.raises(ValueError):metadata('---\n'+header+'\n---\nbody','review')


def test_unclosed_header():
    with pytest.raises(ValueError):metadata('---\nname: review','review')
