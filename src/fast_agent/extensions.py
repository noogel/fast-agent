"""显式配置的一次性本地进程工具；不在 Agent 进程内导入扩展代码。"""
import json
import math
import re
from .command_tool import run_command
from .tools import Tool, ToolResult, ToolInputError


def schema_checked(schema,depth=0):
    if depth>4 or not isinstance(schema,dict): raise ValueError('扩展参数 schema 无效或嵌套超过 4 层')
    kind=schema.get('type')
    common={'type','description'}
    if kind=='object':
        if set(schema)-common-{'properties','required','additionalProperties'}:raise ValueError('不支持的对象 schema 字段')
        properties=schema.get('properties',{});required=schema.get('required',[])
        if (not isinstance(properties,dict) or len(properties)>20 or not isinstance(required,list)
                or any(not isinstance(k,str) or k not in properties for k in required)
                or len(set(required))!=len(required) or schema.get('additionalProperties',False) is not False):
            raise ValueError('扩展对象 schema 无效')
        for key,value in properties.items():
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',key):raise ValueError('扩展字段名无效')
            schema_checked(value,depth+1)
        schema['properties']=properties
        schema['required']=required
        schema['additionalProperties']=False
    elif kind=='array':
        if set(schema)-common-{'items'}:raise ValueError('不支持的数组 schema 字段')
        schema_checked(schema.get('items'),depth+1)
    elif kind in ('string','integer','number','boolean'):
        if set(schema)-common:raise ValueError('不支持的参数 schema 字段')
    else: raise ValueError('不支持的参数 schema 类型')
    if 'description' in schema and (not isinstance(schema['description'],str) or len(schema['description'])>1000):
        raise ValueError('schema 说明无效')


def invoke(spec,args,context):
    try: payload=json.dumps(args,ensure_ascii=False,allow_nan=False)
    except ValueError:raise ToolInputError('扩展参数包含非有限数值') from None
    if len(payload.encode('utf-8'))>32768:raise ToolInputError('扩展参数超过 32 KiB')
    result=run_command({'argv':[*spec['argv'],payload],'timeout':spec.get('timeout',30)},context)
    if not result.ok:
        if 'returncode' in result.data:
            result.data['uncertain']=True
            result.error='扩展进程未正常完成，可能已有副作用；请核实日志，不自动重试。'+(result.error or '')
        return result
    log_id=result.data.get('log_id')
    try:
        if result.data.get('truncated'):raise ValueError('truncated')
        def reject_constant(value): raise ValueError('非有限数值')
        reply=json.loads(result.data['output'], parse_constant=reject_constant)
        json.dumps(reply, allow_nan=False)  # 也拒绝 1e400 等溢出为 inf 的数值
        if (not isinstance(reply,dict) or set(reply)!={'ok','data','error'}
                or type(reply['ok']) is not bool or not isinstance(reply['data'],dict)
                or not (reply['error'] is None or isinstance(reply['error'],str))
                or (not reply['ok'] and not reply['error'])):
            raise ValueError('invalid envelope')
    except (ValueError,TypeError,RecursionError):
        return ToolResult(False,{'uncertain':True,'log_id':log_id},
                          '扩展输出无效或超过 4 KiB；可能已有副作用，请核实日志，不自动重试')
    return ToolResult(reply['ok'],{'result':reply['data'],'log_id':log_id},reply['error'])


def load_extensions(path):
    if path is None:return []
    with path.open('rb') as stream:raw=stream.read(65537)
    if len(raw)>65536:raise ValueError('扩展配置超过 64 KiB')
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!={'tools'} or not isinstance(data['tools'],list) or not 1<=len(data['tools'])<=16:
        raise ValueError('扩展配置需要 1～16 个 tools')
    result=[];names=set()
    for spec in data['tools']:
        if not isinstance(spec,dict) or set(spec)-{'name','description','argv','parameters','timeout'}:
            raise ValueError('扩展配置包含未知字段')
        name=spec.get('name');description=spec.get('description');argv=spec.get('argv');timeout=spec.get('timeout',30)
        if not isinstance(name,str) or not re.fullmatch('[a-zA-Z0-9_-]{1,48}',name) or name in names:
            raise ValueError('扩展名称无效或重复')
        if not isinstance(description,str) or not description.strip() or len(description)>1000:
            raise ValueError('扩展说明需要 1～1000 字符')
        if (not isinstance(argv,list) or not 1<=len(argv)<=64 or not argv[0]
                or any(not isinstance(v,str) or '\0' in v or len(v)>4000 for v in argv)):
            raise ValueError('扩展 argv 无效')
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=60:
            raise ValueError('扩展超时必须大于 0 且不超过 60 秒')
        schema=spec.get('parameters')
        schema_checked(schema)
        if schema['type']!='object':raise ValueError('扩展根参数必须是 object')
        names.add(name)
        # 外部程序不能自称只读而绕过审批或计划模式。
        result.append(Tool('ext_'+name,description+'（外部进程，需要命令授权）',schema,
                           lambda args,ctx,s=spec:invoke(s,args,ctx),'write'))
    return result
