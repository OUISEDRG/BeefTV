"""Per-model reasoning preferences, persisted atomically and pinned per turn."""
import copy
import json
import os
from pathlib import Path
import threading

from completion_bridge import BridgeError
from model_catalog import CatalogError


class ReasoningSettings:
    def __init__(self,path,catalog,initial_overrides=None):
        self.path=Path(path)
        self.catalog=catalog
        self.lock=threading.RLock()
        if self.path.exists():
            self.data=json.loads(self.path.read_text('utf-8'))
            if (not isinstance(self.data,dict) or self.data.get('version')!=1 or
                not isinstance(self.data.get('overrides'),dict) or
                type(self.data.get('revision')) is not int):
                raise ValueError('Codex 推理设置文件格式无效，未重置已有设置。')
            # Retain preferences for models temporarily absent from the catalog.
            for model,effort in self.data['overrides'].items():
                if not isinstance(model,str) or not isinstance(effort,str):
                    raise ValueError('Codex 推理设置文件内容无效。')
                if model in self.catalog.model_ids: self.catalog.validate(model,effort)
        else:
            initial=dict(initial_overrides or {})
            for model,effort in initial.items(): self.catalog.validate(model,effort)
            self.data={'version':1,'revision':0,'overrides':initial}
            self._write(self.data)

    def _write(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_name(self.path.name+f'.{os.getpid()}.{threading.get_ident()}.tmp')
        try:
            with tmp.open('w',encoding='utf-8',newline='\n') as file:
                json.dump(data,file,ensure_ascii=False,indent=2)
                file.flush(); os.fsync(file.fileno())
            os.replace(tmp,self.path)
        finally: tmp.unlink(missing_ok=True)

    def snapshot(self):
        with self.lock:
            return {'models':self.catalog.list_models(),'overrides':copy.deepcopy(self.data['overrides']),
                    'revision':self.data['revision']}

    def update(self,model,effort,expected_revision):
        with self.lock:
            if type(expected_revision) is not int or expected_revision!=self.data['revision']:
                raise BridgeError('设置已在其他页面更新，请刷新后再保存。',409,'settings_conflict')
            try: self.catalog.validate(model,effort)
            except CatalogError as exc: raise BridgeError(str(exc),400,'unsupported_reasoning') from exc
            next_data=copy.deepcopy(self.data)
            if effort is None: next_data['overrides'].pop(model,None)
            else: next_data['overrides'][model]=effort
            next_data['revision']+=1
            self._write(next_data)
            self.data=next_data
            return self.snapshot()

    def resolve(self,model,request_effort=None):
        with self.lock:
            # This page is authoritative, including "follow model default".
            # BeefTV's legacy thinking=true -> medium must not override it.
            selected=self.data['overrides'].get(model)
            try: return self.catalog.validate(model,selected)[1]
            except CatalogError as exc: raise BridgeError(str(exc),400,'unsupported_reasoning') from exc

    def actual_model(self,model):
        try: return self.catalog.get_model(model)['model']
        except CatalogError as exc: raise BridgeError(str(exc),404,'model_not_found') from exc
