"""具名任务计划：原子保存、乐观版本控制；状态不替代执行证据。"""
import json
import re
from .runtime_store import RuntimeStore
from .tools import Tool, ToolResult, ToolInputError, object_schema


def validate_plan(title, steps):
    if not isinstance(title,str) or not title.strip() or len(title)>200:
        raise ToolInputError('计划标题需要 1～200 字符')
    if not isinstance(steps,list) or not 1<=len(steps)<=50:
        raise ToolInputError('计划需要 1～50 个步骤')
    ids=set(); active=0
    for step in steps:
        if not isinstance(step,dict) or set(step)!={'id','title','status','note'}:
            raise ToolInputError('步骤必须包含 id、title、status、note')
        if not isinstance(step['id'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',step['id']) or step['id'] in ids:
            raise ToolInputError('步骤编号无效或重复')
        ids.add(step['id'])
        if not isinstance(step['title'],str) or not step['title'].strip() or len(step['title'])>200:
            raise ToolInputError('步骤标题需要 1～200 字符')
        if not isinstance(step['note'],str) or len(step['note'])>2000:
            raise ToolInputError('步骤说明最多 2000 字符')
        if step['status'] not in ('pending','in_progress','completed','blocked'):
            raise ToolInputError('步骤状态必须为 pending/in_progress/completed/blocked')
        active+=step['status']=='in_progress'
    if active>1:
        raise ToolInputError('同一计划最多一个步骤正在执行')


class PlanStore:
    def __init__(self,root): self.store=RuntimeStore(root,'plans')

    def filename(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',name):
            raise ToolInputError('计划名称只允许字母、数字、下划线、短横线，最多 64 字符')
        return name+'.json'

    def load(self,name):
        filename=self.filename(name)
        if not self.store.path(filename,create=False).exists():
            return {'name':name,'revision':0,'title':'','steps':[]}
        data=self.store.read(filename)
        if (not isinstance(data,dict) or data.get('name')!=name
                or data.get('workspace')!=str(self.store.root)
                or type(data.get('revision')) is not int or data['revision']<1):
            raise ToolInputError('计划记录格式或工作目录无效')
        validate_plan(data.get('title'),data.get('steps'))
        return data

    def list(self):
        return [{'name':name,'revision':(p:=self.load(name))['revision'],'title':p['title'],
                 'statuses':[s['status'] for s in p['steps']]} for name in self.store.names()]


def update_plan(args,context):
    validate_plan(args['title'],args['steps'])
    if type(args['expected_revision']) is not int or args['expected_revision']<0:
        raise ToolInputError('期望版本必须是非负整数')
    store=PlanStore(context.root)
    filename=store.filename(args['name'])
    with store.store.lock():
        current=store.load(args['name'])
        if current['revision']!=args['expected_revision']:
            raise ToolInputError('计划版本已变化，请重新 get_plan 后更新')
        proposed={'name':args['name'],'workspace':str(store.store.root),
                  'revision':current['revision']+1,'title':args['title'],'steps':args['steps']}
        preview='更新任务计划 '+args['name']+'\n'+json.dumps(proposed,ensure_ascii=False,indent=2)
        if not context.authorize('write','plan:'+args['name'],preview):
            return ToolResult(False,error='任务计划更新未获批准')
        if store.load(args['name'])!=current:
            raise ToolInputError('确认期间计划发生变化，请重新读取')
        store.store.write(filename,proposed)
    return ToolResult(True,proposed)


def plan_tools():
    string={'type':'string'}
    step=object_schema({'id':string,'title':string,'status':string,'note':string},['id','title','status','note'])
    return [Tool('get_plan','读取具名持久计划；不存在时 revision=0；状态不代表执行已验证',
                 object_schema({'name':string},['name']),lambda args,ctx:ToolResult(True,PlanStore(ctx.root).load(args['name'])),'read'),
            Tool('list_plans','列出本工作区持久计划摘要',object_schema({},[]),
                 lambda args,ctx:ToolResult(True,{'plans':PlanStore(ctx.root).list()}),'read'),
            Tool('update_plan','经确认保存计划；先 get_plan，expected_revision 必须匹配；状态 pending/in_progress/completed/blocked，最多一个 in_progress；完成必须依据真实工具结果说明',
                 object_schema({'name':string,'expected_revision':{'type':'integer'},'title':string,
                                'steps':{'type':'array','items':step}},['name','expected_revision','title','steps']),update_plan)]
