"""批次先记账，恢复仅核实报告，永不自动重新请求模型。"""
import uuid
from .runtime_store import RuntimeStore
from .delegation_reports import DelegationReports


class DelegationBatches:
    def __init__(self,root):
        self.store=RuntimeStore(root,'delegation-batches')
        self.reports=DelegationReports(root)

    def start(self,tasks):
        record={'id':uuid.uuid4().hex,'status':'running','tasks':[
            {'task':task,'report_id':uuid.uuid4().hex} for task in tasks]}
        self.store.write(record['id']+'.json',record)
        return record

    def load(self,identifier):
        self.reports.filename(identifier)
        record=self.store.read(identifier+'.json')
        if not isinstance(record,dict) or record.get('id')!=identifier or record.get('status') not in {'running','completed','incomplete'}:
            raise ValueError('委派批次状态无效')
        rows=record.get('tasks')
        if not isinstance(rows,list) or not 1<=len(rows)<=5:
            raise ValueError('委派批次任务无效')
        ids=[]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('task'),str) or not 1<=len(row['task'])<=8000:
                raise ValueError('委派批次任务无效')
            self.reports.filename(row.get('report_id'))
            ids.append(row['report_id'])
        if len(set(ids))!=len(ids):raise ValueError('重复委派报告引用')
        return record

    def finish(self,identifier):
        record=self.load(identifier)
        evidence=[]
        for row in record['tasks']:
            try:
                report=self.reports.load(row['report_id'])
                if report['task']!=row['task']:raise ValueError('任务不匹配')
                state='completed' if report['completed'] else 'incomplete'
            except (ValueError,OSError):state='missing_or_invalid'
            evidence.append({'report_id':row['report_id'],'state':state})
        record['evidence']=evidence
        record['status']='completed' if all(e['state']=='completed' for e in evidence) else 'incomplete'
        self.store.write(identifier+'.json',record)
        return record

    def reconcile(self,identifier):
        # 活动批次持有同一锁，不能把仍在执行的任务误判为中断。
        with self.store.lock():return self.finish(identifier)
