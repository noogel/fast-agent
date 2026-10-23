"""一次有界真实服务探针；只发送合成文本，不搜索或发送项目内容。"""
import json,os
from fast_agent.config import Config
from openai import OpenAI,APIStatusError


def main():
    config=Config.from_env(os.environ)
    client=OpenAI(api_key=config.api_key,base_url=config.base_url,max_retries=0,timeout=90)
    try:
        try:
            response=client.chat.completions.create(model=config.model,max_tokens=32,
                messages=[{'role':'user','content':'以下为合成长度验收材料，无需解释。只回答OK。\n'+'龘 '*600000}])
            report={'rejected':False,'usage':response.usage.model_dump() if response.usage else None}
        except APIStatusError as exc:
            body=exc.body if isinstance(exc.body,dict) else {}
            error=body.get('error',body)
            report={'rejected':True,'status':exc.status_code,'error_code':error.get('code') if isinstance(error,dict) else None,
                'error_type':error.get('type') if isinstance(error,dict) else None,
                'message':str(error.get('message',''))[:400] if isinstance(error,dict) else ''}
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:client.close()


if __name__=='__main__':main()
