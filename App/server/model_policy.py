"""Local model switches; downloaded files and public source configuration remain separate."""
from ui_strings import source as ui_text
import json
import os
from pathlib import Path
import uuid

from model_requirements import COMPONENTS


class ModelPolicy:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'UserData/Settings/models.policy.json'

    def disabled(self):
        if not self.path.is_file():
            return set()
        data = json.loads(self.path.read_text('utf-8-sig'))
        rows = data.get('disabled')
        if data.get('schema_version') != 1 or not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
            raise ValueError(ui_text('common.model_policy_disabled_read_switches_original_file_preserved_failed'))
        return set(rows)

    def set_enabled(self, ident, enabled):
        disabled = self.disabled()
        disabled.discard(ident) if enabled else disabled.add(ident)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(dict(schema_version=1, disabled=sorted(disabled)), ensure_ascii=False, indent=2), 'utf-8')
        os.replace(temporary, self.path)

    def enabled(self, module, ident):
        disabled = self.disabled()
        if not disabled:
            return True
        component = COMPONENTS.get(module + ':' + ident, 'speech.model.' + ident if module == 'speech' else module + ':' + ident)
        if component in disabled:
            return False
        path = self.root / 'App/config/component-catalog.json'
        if not path.is_file():
            return True
        catalog = json.loads(path.read_text('utf-8-sig'))
        by_id = {row['id']: row for row in catalog['components']}
        if module == 'text':
            return not any(row['id'] in disabled and any(step.get('op') == 'ollama_pull' and step.get('model') == ident for step in row.get('after', [])) for row in by_id.values())
        def allowed(key, seen):
            if key in disabled:
                return False
            if key in seen:
                return True
            return all(allowed(parent, seen | {key}) for parent in by_id.get(key, {}).get('requires', []))
        return allowed(component, set())


def apply_profiles(root, module, profiles, field='available'):
    policy = ModelPolicy(root)
    for row in profiles:
        row['enabled'] = policy.enabled(module, row['id'])
        if not row['enabled']:
            row[field] = False
            message = ui_text('common.model_policy_apply_profiles_disabled_enable_settings_components')
            row['reason'] = message
            if field == 'ready':
                row['status'] = 'disabled'
                row['reasons'] = [message]
    return profiles
