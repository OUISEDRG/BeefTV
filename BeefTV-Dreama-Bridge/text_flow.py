"""Verified text catalog. Execution stays disabled until supported canvas auth exists.

CLI 1.0.5 tokens authenticate image/audio APIs but the production canvas gateway
rejects them. Never fabricate identity headers or advertise a runnable placeholder.
"""
TEXT_BINDING = {'projectId':233227,'projectUuid':'434d8d37e42a5491fc58a4c131a53880'}
TEXT_AUTH_ERROR = '当前 Dreama CLI 1.0.5 凭据不能认证剧梦画布文本接口（HTTP 401）；尚未启用文本生成。'

def text_models(include_disabled=False):
    rows=[('gpt-5.5','GPT-5.5'),('gpt-5.6-terra','GPT-5.6 Terra'),
          ('deepseek-v4-pro','DeepSeek V4 Pro'),('gemini-3.5-flash','Gemini 3.5 Flash'),
          ('gpt-5.4','GPT-5.4'),('doubao-seed-2.1-pro','Doubao Seed 2.1 Pro'),
          ('claude-sonnet-4-6','Claude Sonnet 4.6')]
    return {eid:{'id':eid,'label':name,'maxChars':32000,'enabled':False,
                 'disabledReason':TEXT_AUTH_ERROR} for eid,name in rows} if include_disabled else {}
