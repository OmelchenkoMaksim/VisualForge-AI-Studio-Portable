"""One confined portable layout contract, with compatibility for legacy source tests."""
import json
import os
from pathlib import Path

class PortablePaths:
    def __init__(self,root,document):
        self.root=Path(root).resolve();self.modern=True
        if document.get('schema_version')!=1:raise ValueError('Unsupported portable layout')
        self.entries={}
        for key,value in document['paths'].items():
            if not isinstance(value,str) or Path(value).is_absolute() or ':' in value:raise ValueError('Layout paths must be relative')
            path=(self.root/value).resolve()
            if not path.is_relative_to(self.root):raise ValueError('Layout path escapes portable root')
            self.entries[key]=path
        self.storage_base=self.root
        self.engine_workflows=self.entries['workflows']
    def __getattr__(self,key):
        if key in self.entries:return self.entries[key]
        raise AttributeError(key)
    @classmethod
    def discover(cls,module_file):
        module=Path(module_file).resolve();override=os.environ.get('VISUALFORGE_PORTABLE_ROOT')
        roots=[Path(override)] if override else list(module.parents)
        for root in roots:
            config=root/'App/config/portable-layout.json'
            if config.is_file():return cls(root,json.loads(config.read_text(encoding='utf-8-sig')))
        if override:raise ValueError('Configured portable layout is missing')
        obj=cls.__new__(cls);obj.modern=False;obj.root=module.parent.parent;obj.storage_base=obj.root
        base=module.parent;runtime=obj.root/'ComfyUI_windows_portable'
        obj.entries={'server':base,'frontend':base/'static','workflows':base,'runtime':runtime,'comfy':runtime/'ComfyUI','python':runtime/'python_embeded/python.exe','models':runtime/'ComfyUI/models','settings':obj.root,'prompts':obj.root,'logs':obj.root/'logs','profiles':obj.root/'browser-profiles','results':obj.root/'Results','cache':obj.root/'cache','backups':obj.root/'backups','state':obj.root}
        obj.engine_workflows=base/'workflows';return obj

def from_server():return PortablePaths.discover(Path(__file__))
