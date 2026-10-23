"""显式配置、逐次批准的 LSP 查询；每次查询独立启动并关闭语言服务。"""
import json
import math
import re
from .command_sandbox import command_launch
from .file_tools import resolve_file,read_bytes,fingerprint
from .lsp_transport import LSPClient
from .tools import Tool,ToolResult,ToolInputError,object_schema


def lsp_tools(config):
    if config is None:return []
    with config.open('rb') as stream:raw=stream.read(65537)
    if len(raw)>65536:raise ValueError('LSP 配置超过 64 KiB')
    spec=json.loads(raw)
    if not isinstance(spec,dict) or set(spec)-{'argv','language_id','extensions','timeout'}:
        raise ValueError('LSP 配置字段无效')
    argv=spec.get('argv');language=spec.get('language_id');extensions=spec.get('extensions')
    timeout=spec.get('timeout',10)
    if not isinstance(argv,list) or not 1<=len(argv)<=64 or any(not isinstance(s,str) or not s or len(s)>4000 or '\0' in s for s in argv):
        raise ValueError('LSP argv 无效')
    if not isinstance(language,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',language):
        raise ValueError('LSP language_id 无效')
    if not isinstance(extensions,list) or not 1<=len(extensions)<=20 or any(not isinstance(s,str) or not re.fullmatch(r'\.[a-zA-Z0-9]{1,16}',s) for s in extensions):
        raise ValueError('LSP extensions 无效')
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=60:
        raise ValueError('LSP timeout 必须在 0～60 秒之间')

    def handler(kind):
        def run(args,context):
            path=resolve_file(context,args['path'])
            if path.suffix not in extensions:raise ToolInputError('文件扩展名不在 LSP 配置范围')
            before=read_bytes(path);text=before.decode('utf-8')
            position=None
            if kind=='definition':
                lines=text.splitlines();line=args['line'];column=args['column']
                if not 1<=line<=len(lines) or not 1<=column<=len(lines[line-1])+1:
                    raise ToolInputError('行列从 1 开始，必须位于文件内')
                position={'line':line-1,'character':len(lines[line-1][:column-1].encode('utf-16-le'))//2}
            from .tasks import TaskStore
            if TaskStore(context.root).blockers():raise ToolInputError('存在未核实操作，不能启动 LSP')
            target={'argv':argv,'path':str(path),'operation':kind}
            if not context.authorize('lsp_start',target,'启动语言服务（可读取项目文件，沿用命令沙箱） '+json.dumps(target,ensure_ascii=False)+'？'):
                raise ToolInputError('LSP 启动未获授权')
            if read_bytes(path)!=before:raise ToolInputError('确认期间文件发生变化')
            launch,env=command_launch(argv,context.root,context.sandbox)
            client=LSPClient(launch,env,context.root.resolve(),timeout)
            try:
                capabilities=client.initialize()
                capability={'symbols':'documentSymbolProvider','definition':'definitionProvider'}.get(kind)
                if capability and capabilities.get(capability) in (None,False):
                    raise ValueError('语言服务未声明该查询能力')
                uri=path.as_uri()
                client.send({'jsonrpc':'2.0','method':'textDocument/didOpen','params':{
                    'textDocument':{'uri':uri,'languageId':language,'version':1,'text':text}}})
                if kind=='diagnostics':result=client.wait_diagnostics(uri,1)
                else:
                    params={'textDocument':{'uri':uri}}
                    if position is not None:params['position']=position
                    result=client.request('textDocument/'+('documentSymbol' if kind=='symbols' else 'definition'),params)
                if read_bytes(path)!=before:raise ValueError('分析期间文件发生变化，请重新查询')
                return ToolResult(True,{'path':args['path'],'sha256':fingerprint(before),'position_encoding':'utf-16',
                    'result':result,'result_trusted':False})
            except (ValueError,OSError):
                return ToolResult(False,error='LSP 查询失败、超时或文件变化；未确认取得当前结果')
            finally:client.shutdown()
        return run
    properties={'path':{'type':'string'}}
    return [
        Tool('lsp_symbols','查询当前文件符号；需批准启动语言服务',object_schema(properties,['path']),handler('symbols')),
        Tool('lsp_definition','查询定义；输入行列从1开始，列按Unicode字符计数；结果位置为LSP零基UTF-16',object_schema({**properties,'line':{'type':'integer'},'column':{'type':'integer'}},['path','line','column']),handler('definition')),
        Tool('lsp_diagnostics','等待当前文件版本诊断；超时不表示没有错误',object_schema(properties,['path']),handler('diagnostics'))]
