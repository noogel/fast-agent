"""显式配置的检查命令与持久执行证据；配置不等于执行授权。"""
from dataclasses import asdict
import json
import math
import re
import time
import uuid
from .command_tool import run_command
from .runtime_store import RuntimeStore
from .tools import Tool, ToolResult, ToolInputError, object_schema


def load_checks(path):
    if path is None: return {}
    with path.open('rb') as stream: raw=stream.read(65537)
    if len(raw)>65536: raise ValueError('检查配置不能超过 64 KiB')
    data=json.loads(raw)
    if not isinstance(data,dict) or not 1<=len(data)<=32:
        raise ValueError('检查配置需要 1～32 个命名检查')
    result={}
    for name,spec in data.items():
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',name) or not isinstance(spec,dict) or set(spec)-{'argv','timeout'}:
            raise ValueError('检查名称或字段无效')
        argv=spec.get('argv');timeout=spec.get('timeout',60)
        if (not isinstance(argv,list) or not 1<=len(argv)<=128 or not argv[0]
                or any(not isinstance(v,str) or '\0' in v or len(v)>4000 for v in argv)):
            raise ValueError('检查 argv 必须是非空字符串数组')
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=60:
            raise ValueError('检查超时必须大于 0 且不超过 60 秒')
        result[name]={'argv':list(argv),'timeout':timeout}
    return result


class CheckRuns:
    def __init__(self,root): self.store=RuntimeStore(root,'checks')

    def load(self,ident):
        if not re.fullmatch('[0-9a-f]{32}',ident): raise ToolInputError('检查运行编号无效')
        record=self.store.read(ident+'.json')
        if (not isinstance(record,dict) or record.get('id')!=ident
                or record.get('workspace')!=str(self.store.root)
                or record.get('status') not in {'running','passed','failed','unknown','interrupted'}
                or not isinstance(record.get('results'),list)):
            raise ToolInputError('检查运行记录格式无效')
        return record

    def list(self):
        return [{'id':ident,'status':(r:=self.load(ident))['status'],'names':r['names'],
                 'started':r['started']} for ident in self.store.names()]


def run_checks(args,context):
    names=args['names']
    if not 1<=len(names)<=32 or len(set(names))!=len(names) or any(n not in context.quality_checks for n in names):
        raise ToolInputError('检查名称必须存在、非空且不能重复，请先 list_checks')
    # 固定启动配置中的 argv；工具参数无法注入新的命令或改动超时。
    selected={name:context.quality_checks[name] for name in names}
    runs=CheckRuns(context.root)
    record={'id':uuid.uuid4().hex,'workspace':str(context.root.resolve()),'names':names,
            'status':'running','started':time.time(),'sandbox':context.sandbox,'results':[]}
    runs.store.write(record['id']+'.json',record)
    for name,spec in selected.items():
        start=time.monotonic()
        try:
            result=run_command(spec,context)
        except BaseException as exc:
            record['status']='interrupted' if isinstance(exc,KeyboardInterrupt) else 'unknown'
            record['finished']=time.time()
            record['results'].append({'name':name,'argv':spec['argv'],'timeout':spec['timeout'],
                'elapsed_seconds':round(time.monotonic()-start,3),
                'result':asdict(ToolResult(False, {'uncertain':True}, '检查执行异常或中断；结果未确认，不自动重跑'))})
            runs.store.write(record['id']+'.json',record)
            raise
        record['results'].append({'name':name,'argv':spec['argv'],'timeout':spec['timeout'],
                                  'elapsed_seconds':round(time.monotonic()-start,3),'result':asdict(result)})
        runs.store.write(record['id']+'.json',record)
        if not result.ok: break  # 首个失败停止，剩余检查没有执行，不能宣称通过。
    passed=len(record['results'])==len(names) and all(r['result']['ok'] for r in record['results'])
    record['status']='passed' if passed else 'failed'
    record['finished']=time.time()
    runs.store.write(record['id']+'.json',record)
    return ToolResult(passed,{'run_id':record['id'],**record},None if passed else '质量检查未全部通过，请检查执行结果；不能声称检查通过')


def check_tools():
    string={'type':'string'}
    return [Tool('list_checks','列出用户启动时显式配置的质量检查命令；配置不代表授权',object_schema({},[]),
                 lambda args,ctx:ToolResult(True,{'checks':ctx.quality_checks}),'read'),
            Tool('run_checks','运行指定的配置检查，逐项遵守命令审批和沙箱；首个失败停止，持久保存真实退出码与日志编号',
                 object_schema({'names':{'type':'array','items':string}},['names']),run_checks),
            Tool('get_check_run','读取历史检查证据；只反映当时执行结果，不证明当前文件仍通过',
                 object_schema({'run_id':string},['run_id']),lambda args,ctx:ToolResult(True,CheckRuns(ctx.root).load(args['run_id'])),'read')]
