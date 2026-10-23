"""工具结果进入模型历史前统一限额；完整内容有界落盘，按字符分页。"""
from dataclasses import asdict
import hashlib
import json
import re
import uuid
from .runtime_store import RuntimeStore
from .tools import Tool, ToolResult, object_schema

INLINE_LIMIT=16000
STORAGE_LIMIT=1024*1024


def retain_result(root,result):
    serialized=json.dumps(asdict(result),ensure_ascii=False)
    if len(serialized)<=INLINE_LIMIT:return result
    encoded=serialized.encode('utf-8')
    retained=encoded[:STORAGE_LIMIT].decode('utf-8',errors='ignore')
    truncated=len(encoded)>STORAGE_LIMIT
    data={'output_ref':None,'preview':serialized[:2000],'original_chars':len(serialized),
          'notice':'结果过长，历史仅含预览；使用 read_tool_output 分页读取。截断内容不能当作完整证据。'}
    # 保留控制信号及常用执行状态，不能让输出裁剪改变操作结果。
    for key in ('uncertain','returncode','status'):
        value=result.data.get(key)
        if type(value) in (bool,int) or (isinstance(value,str) and len(value)<=100):data[key]=value
    try:
        identifier=uuid.uuid4().hex
        record={'version':1,'content':retained,'storage_truncated':truncated,
                'sha256':hashlib.sha256(encoded).hexdigest(),'original_chars':len(serialized)}
        RuntimeStore(root,'tool-outputs').write(identifier+'.json',record)
        data['output_ref']={'id':identifier,'stored_chars':len(retained),'storage_truncated':truncated,
                            'sha256':record['sha256']}
    except (ValueError,OSError):
        data['storage_error']=True
        data['notice']='结果过长且落盘失败，仅保留预览；原操作状态不变，请缩小查询范围重新核实，勿重放写操作。'
    return ToolResult(result.ok,data,result.error[:1000] if result.error else result.error)


def read_output(args,context):
    identifier=args['output_id'];offset=args.get('offset',0);limit=args.get('limit',4000)
    if not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('工具结果 ID 无效')
    if offset<0 or not 1<=limit<=4000:raise ValueError('offset 必须非负，limit 为 1～4000 字符')
    record=RuntimeStore(context.root,'tool-outputs').read(identifier+'.json')
    if (not isinstance(record,dict) or record.get('version')!=1 or not isinstance(record.get('content'),str)
            or len(record['content'].encode('utf-8'))>STORAGE_LIMIT or type(record.get('storage_truncated')) is not bool):
        raise ValueError('工具结果记录无效')
    content=record['content'];start=min(offset,len(content));end=min(start+limit,len(content))
    return ToolResult(True,{'content':content[start:end],'offset':start,'next_offset':end,
                          'eof':end==len(content),'storage_truncated':record['storage_truncated']})


def output_tool():
    return Tool('read_tool_output','按字符分页读取已保存的工具结果 JSON；limit 最大4000；eof 仅表示已存内容读完，storage_truncated 表示原结果未完整保存。',
                object_schema({'output_id':{'type':'string'},'offset':{'type':'integer'},'limit':{'type':'integer'}},['output_id']),read_output,'read')
