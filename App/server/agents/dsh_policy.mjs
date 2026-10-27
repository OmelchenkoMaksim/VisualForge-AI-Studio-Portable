// The studio's permission levels for DeepSeek Harness, as a Cordis plugin on dsh's `tools/pre-execute` hook.
//
// dsh's own model is a sandbox (Windows: restricted token + ACLs) plus approval only for leaving it, so inside the project
// it edits files and runs commands without asking. The studio's levels promise more ("ask" = every edit and command asks),
// so this hook decides each tool call after dsh's own listeners: `deny` reaches the model as a refused call, `ask` becomes
// an ACP session/request_permission the studio shows as a permission card. Refusal reasons start with "VisualForge:" so the
// studio can tell them from other tool failures.
import path from 'node:path';

export const name = 'visualforge-policy';

const READ = new Set(['read', 'read_image', 'glob', 'grep', 'todo_write', 'create_goal', 'get_goal', 'update_goal', 'skill',
  'job_list', 'job_output', 'working_directory', 'exit_plan_mode', 'list_mcp_resources', 'list_mcp_resource_templates', 'read_mcp_resource']);
const EDIT = new Set(['edit', 'write', 'str_replace_editor']);
const SHELL = new Set(['pwsh', 'bash']);
const PUSH = /\bgit\b[^\n;&|]*\bpush\b/i;
// The commands the `auto` level runs without asking, as for the other agents.
const AUTO = /^git (status|diff|log|add|commit)(\s|$)/;

function deny(reason) {
  return { kind: 'deny', reason: 'VisualForge: ' + reason };
}

export function apply(ctx, config) {
  const mode = config.mode;
  const root = path.resolve(config.workspace);
  const inside = (value) => {
    if (typeof value !== 'string' || value === '') return true;
    const full = path.resolve(root, value);
    const relative = path.relative(root, full);
    return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
  };

  ctx.on('tools/pre-execute', async (exec, next) => {
    const downstream = await next();
    if (downstream.kind !== 'allow') return downstream;
    const args = exec.arguments && typeof exec.arguments === 'object' ? exec.arguments : {};
    const target = args.file_path ?? args.path;
    if (READ.has(exec.name)) {
      return inside(target) ? downstream : deny('the file is outside the project folder');
    }
    if (EDIT.has(exec.name)) {
      if (!inside(target)) return deny('the file is outside the project folder');
      if (mode === 'read') return deny('editing files is not allowed in this chat');
      return mode === 'auto' ? downstream : { kind: 'ask', reason: 'VisualForge: edit ' + String(target ?? '') };
    }
    if (SHELL.has(exec.name)) {
      const command = String(args.command ?? '');
      if (PUSH.test(command)) return deny('git push is not allowed');
      if (mode === 'read') return deny('running commands is not allowed in this chat');
      const lines = command.split(/\r?\n|;|&&/).map((line) => line.trim()).filter(Boolean);
      if (mode === 'auto' && lines.length > 0 && lines.every((line) => AUTO.test(line))) return downstream;
      return { kind: 'ask', reason: 'VisualForge: run ' + command.slice(0, 200) };
    }
    if (exec.name === 'job_kill') return mode === 'read' ? deny('not allowed in this chat') : downstream;
    return mode === 'read' ? deny('not allowed in this chat') : { kind: 'ask', reason: 'VisualForge: ' + exec.name };
  });
}
