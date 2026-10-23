"""可信本地评测任务的报告汇总；任务和断言不由被评测模型生成。"""
import time,ast
from datetime import datetime,timezone

USAGE_KEYS=('requests','unknown_requests','prompt_tokens','completion_tokens','total_tokens')

class EvaluationFailure(Exception):
    """可信runner在finally关闭客户端之前保存用量；不传播原始异常正文。"""
    def __init__(self,category,usage):
        super().__init__('评测任务失败')
        self.category=category
        self.usage=usage


def addition_checks(source):
    """engineering-v2固定形状判定；仅解析，绝不执行生成代码。"""
    try:
        tree=ast.parse(source)
        expected=ast.parse('def add(a, b):\n return a + b\n')
        exact=ast.dump(tree,include_attributes=False)==ast.dump(expected,include_attributes=False)
        return {'syntax_valid':True,'exact_addition_contract':exact}
    except (SyntaxError,ValueError,RecursionError):
        return {'syntax_valid':False,'exact_addition_contract':False}


def checked_usage(value):
    if not isinstance(value,dict):return None
    if any(type(value.get(key)) is not int or value[key]<0 for key in USAGE_KEYS):return None
    if value['unknown_requests']>value['requests'] or value['total_tokens']!=value['prompt_tokens']+value['completion_tokens']:return None
    return {key:value[key] for key in USAGE_KEYS}


def evaluate_suite(version,cases,*,model):
    """每个可信runner返回completed/checks/usage；异常不阻止其余任务。"""
    cases=list(cases)
    names=[name for name,_ in cases]
    if not isinstance(version,str) or not version or not isinstance(model,str) or not model:
        raise ValueError('评测必须声明任务集版本和模型')
    if not 1<=len(cases)<=100 or any(not isinstance(name,str) or not name for name in names) or len(set(names))!=len(names):
        raise ValueError('评测需要1～100个唯一命名任务')
    rows=[]
    for name,run in cases:
        started=time.monotonic()
        row={'name':name,'passed':False,'cost':None,'cost_reason':'未配置可核验价格；不把未知费用当作零'}
        try:
            result=run()
            checks=result['checks']
            if not isinstance(checks,dict) or not checks or any(not isinstance(k,str) or type(v) is not bool for k,v in checks.items()):
                raise ValueError('独立断言必须是非空布尔映射')
            row.update(status='completed',checks=checks,agent_completed=result.get('completed') is True,
                passed=result.get('completed') is True and all(checks.values()),usage=checked_usage(result.get('usage')))
        except EvaluationFailure as exc:
            row.update(status='error',error_type='EvaluationFailure',usage=checked_usage(exc.usage))
        except Exception as exc:
            row.update(status='error',error_type=type(exc).__name__,usage=None)
        row['elapsed_seconds']=round(time.monotonic()-started,6)
        rows.append(row)
    usage={key:sum(row['usage'][key] for row in rows) for key in USAGE_KEYS} if all(row['usage'] is not None for row in rows) else None
    return {'schema_version':1,'suite_version':version,'model':model,'created_at':datetime.now(timezone.utc).isoformat(),
        'total':len(rows),'passed':sum(row['passed'] for row in rows),'usage':usage,'cost':None,'cases':rows}


def recovery_checks(before_tools, after_tools, expected):
    """只接受实际读取事件；最终回答和失败结果里的代号不能代替成功读取。"""
    import json
    return {
        'observed_read_failure': any(item['name']=='read_file' and item['result']['ok'] is False for item in before_tools),
        'observed_recovered_read': any(item['name']=='read_file' and item['result']['ok'] is True
            and expected in json.dumps(item['result'],ensure_ascii=False) for item in after_tools),
    }
