"""持久需求澄清；只保存数据，不授予任何执行权限。"""
import math
import time
import uuid
import re
from .runtime_store import RuntimeStore


class Questions:
    def __init__(self,root):self.store=RuntimeStore(root,'questions')

    def filename(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('问题 ID 无效')
        return identifier+'.json'

    def create(self,question,choices=(),ttl=86400):
        if not isinstance(question,str) or not question.strip() or len(question)>2000:raise ValueError('问题无效')
        if not isinstance(choices,(list,tuple)) or len(choices)>5 or any(not isinstance(c,str) or not c.strip() or len(c)>200 for c in choices):raise ValueError('问题选项无效')
        if type(ttl) is not int or not 1<=ttl<=604800:raise ValueError('有效期需为 1～604800 秒')
        identifier=uuid.uuid4().hex
        record={'id':identifier,'question':question,'choices':list(choices),'status':'pending',
                'expires_at':time.time()+ttl,'answer':None,'authorization':False}
        self.store.write(self.filename(identifier),record)
        return record

    def load(self,identifier):
        record=self.store.read(self.filename(identifier))
        if (not isinstance(record,dict) or record.get('id')!=identifier or record.get('authorization') is not False
            or record.get('status') not in {'pending','answered'} or type(record.get('expires_at')) not in (int,float)):
            raise ValueError('问题记录无效')
        if not math.isfinite(record['expires_at']):raise ValueError('问题有效期无效')
        question=record.get('question');choices=record.get('choices')
        if not isinstance(question,str) or not question.strip() or len(question)>2000:raise ValueError('问题正文无效')
        if not isinstance(choices,list) or len(choices)>5 or any(not isinstance(c,str) or not c.strip() or len(c)>200 for c in choices):raise ValueError('问题选项无效')
        if record['status']=='pending' and record.get('answer') is not None:raise ValueError('待回答记录包含答案')
        if record['status']=='answered' and (not isinstance(record.get('answer'),str) or not 1<=len(record['answer'])<=4000):raise ValueError('问题回答无效')
        return record

    def answer(self,identifier,text):
        if not isinstance(text,str) or not text.strip() or len(text)>4000:raise ValueError('回答需为 1～4000 字符')
        with self.store.lock():
            record=self.load(identifier)
            if record['status']!='pending':raise ValueError('问题已回答，不能覆盖')
            if time.time()>=record['expires_at']:raise ValueError('问题已过期，请重新提问')
            record.update(status='answered',answer=text)
            self.store.write(self.filename(identifier),record)
            return record

    def list(self,page=1):
        import itertools
        if type(page) is not int or page<1:raise ValueError('页码至少为 1')
        paths=list(itertools.islice(self.store.path('index',create=False).parent.glob('*.json'),1001))
        if len(paths)>1000:raise ValueError('问题超过 1000 项，请按 ID 读取')
        rows=[];errors=[]
        for path in sorted(paths)[(page-1)*20:page*20]:
            try:
                record=self.load(path.stem)
                rows.append({'id':record['id'],'question':record['question'],'status':record['status'],
                             'expired':record['status']=='pending' and time.time()>=record['expires_at']})
            except (ValueError,OSError):errors.append(path.stem)
        return {'questions':rows,'invalid':errors,'page':page,'has_more':len(paths)>page*20}
