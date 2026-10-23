"""模型询问需求与执行审批分离；回答不是执行权限。"""
from .tools import Tool,ToolResult,object_schema


def ask_user(args,context):
    question=args['question'];choices=args.get('choices',[])
    if not question.strip() or len(question)>2000:raise ValueError('问题需为 1～2000 字符')
    if len(choices)>5 or any(not c.strip() or len(c)>200 for c in choices):
        raise ValueError('选项最多 5 个，每项 1～200 字符')
    from .questions import Questions
    store=Questions(context.root)
    record=store.create(question,choices)
    def pending():
        return ToolResult(False,{'pending_question':record['id'],'question':question},'等待用户回答；使用 /answer ID 回答，再 /continue-question ID。')
    if context.ask_user is None:return pending()
    answer=context.ask_user(question,choices)
    if answer is None:return pending()
    if not isinstance(answer,str) or not answer.strip() or len(answer)>4000:raise ValueError('回答需为 1～4000 字符')
    store.answer(record['id'],answer)
    return ToolResult(True,{'question_id':record['id'],'answer':answer,'notice':'这是需求澄清，不是命令、编辑或权限提升授权。'})


def terminal_question(question,choices):
    print('\n[Agent 提问] '+question)
    for i,choice in enumerate(choices,1):print(f'{i}. {choice}')
    try:answer=input('输入回答或选项编号（空行跳过）：').strip()
    except EOFError:return None
    if not answer:return None
    if choices and answer.isascii() and answer.isdigit() and len(answer)<=2:
        index=int(answer)
        if 1<=index<=len(choices):return choices[index-1]
    return answer


def question_tool():
    return Tool('ask_user','需求有歧义时询问用户。可提供最多5个选项；回答不会授予执行权限；无交互入口时返回失败。',
        object_schema({'question':{'type':'string'},'choices':{'type':'array','items':{'type':'string'}}},['question']),ask_user,'read')
