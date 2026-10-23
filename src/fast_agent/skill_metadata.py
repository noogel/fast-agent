"""受限 Skill frontmatter：只接收名称和用途，不解释权限字段。"""
import yaml


def metadata(content, directory_name):
    lines=content.removeprefix('\ufeff').splitlines(keepends=True)
    if not lines or lines[0].strip()!='---':
        return {'name':directory_name,'description':''}
    header=[]
    size=0
    for line in lines[1:]:
        if line.strip()=='---':break
        size+=len(line.encode('utf-8'))
        if size>8192:raise ValueError('Skill 元数据超过 8 KiB')
        header.append(line)
    else:raise ValueError('Skill 元数据缺少结束分隔符')
    source=''.join(header)
    try:
        # 先扫描限制结构，避免 YAML 别名、复杂递归或自定义标签。
        count=0
        for token in yaml.scan(source):
            count+=1
            if count>32 or isinstance(token,(yaml.tokens.AliasToken,yaml.tokens.AnchorToken,yaml.tokens.TagToken)):
                raise ValueError('Skill 元数据不支持别名、锚点、标签或复杂结构')
        node=yaml.compose(source,Loader=yaml.SafeLoader)
        if not isinstance(node,yaml.MappingNode):raise ValueError('Skill 元数据必须是键值映射')
        result={}
        for key,value in node.value:
            if (not isinstance(key,yaml.ScalarNode) or not isinstance(value,yaml.ScalarNode)
                    or key.tag!='tag:yaml.org,2002:str' or value.tag!='tag:yaml.org,2002:str'):
                raise ValueError('Skill 元数据字段必须是字符串')
            if key.value not in {'name','description'} or key.value in result:
                raise ValueError('Skill 元数据字段未知或重复；仅支持 name 和 description')
            result[key.value]=value.value.strip()
    except yaml.YAMLError:
        raise ValueError('Skill 元数据 YAML 格式无效') from None
    if result.get('name',directory_name)!=directory_name:
        raise ValueError('Skill name 必须与目录名称一致')
    description=result.get('description','')
    if len(description)>1024 or any(ord(c)<32 and c not in '\n\t' for c in description):
        raise ValueError('Skill description 无效或超过 1024 字符')
    return {'name':directory_name,'description':description}
