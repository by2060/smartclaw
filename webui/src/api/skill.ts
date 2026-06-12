import client from './client';

export interface SkillRequires {
  bins?: string[];
  any_bins?: string[];
  env?: string[];
}

export interface SkillInstallSpec {
  id?: string;
  kind: 'brew' | 'npm' | 'uv' | 'pip' | 'go' | 'download';
  label?: string;
  bins?: string[];
  formula?: string;
  package?: string;
  url?: string;
}

export interface Skill {
  name: string;
  description: string;
  location: string;
  source?: string;
  content?: string;
  category?: string;
  // Eligibility
  eligible?: boolean;
  missing?: string[];
  requires?: SkillRequires;
  install_specs?: SkillInstallSpec[];
}

export interface Command {
  name: string;
  canonical_name: string;
  description: string;
  template: string;
  agent?: string;
  model?: string;
  subtask?: boolean;
  hidden: boolean;
  aliases: string[];
  visible_surfaces: string[];
  execution_kind: 'direct' | 'llm' | 'session_control';
  allow_attachments: boolean;
  requires_existing_session: boolean;
  channel_safe: boolean;
}

export interface SkillInstallRequest {
  /** Install source: clawhub:<name>, github:<owner>/<repo>, workspace:<path>, https://..., /local/path */
  source: string;
  /** 'global' (default) or 'project' */
  scope?: string;
  /** Mark installed project skill as deletable via DELETE /api/skills/{name}. */
  deletable?: boolean;
}

export interface SkillRequestContext {
  agent?: string;
  sessionId?: string;
  category?: string;
}

function skillRequestParams(context?: SkillRequestContext) {
  if (!context) return undefined;
  return {
    agent: context.agent,
    session_id: context.sessionId,
    category: context.category,
  };
}

export interface SkillInstallResponse {
  success: boolean;
  skill_name?: string;
  location?: string;
  message: string;
  error?: string;
}

export interface DepInstallSpecResult {
  success: boolean;
  spec_id?: string;
  command: string[];
  stdout: string;
  stderr: string;
  returncode: number;
  error?: string;
}

export interface DepInstallResponse {
  results: DepInstallSpecResult[];
}

export const skillAPI = {
  list: (context?: SkillRequestContext) =>
    client.get<Skill[]>('/api/skills', { params: skillRequestParams(context) }),

  /** List all skills with eligibility status (bin/env checks). */
  status: (context?: SkillRequestContext) =>
    client.get<Skill[]>('/api/skills/status', { params: skillRequestParams(context) }),

  get: (name: string, context?: SkillRequestContext) =>
    client.get<Skill>(`/api/skills/${name}`, { params: skillRequestParams(context) }),

  create: (data: {
    name: string;
    description: string;
    content: string;
  }, context?: SkillRequestContext) =>
    client.post<Skill>('/api/skills', data, { params: skillRequestParams(context) }),

  update: (name: string, data: {
    name: string;
    description: string;
    content: string;
  }, context?: SkillRequestContext) =>
    client.put<Skill>(`/api/skills/${name}`, data, { params: skillRequestParams(context) }),

  delete: (name: string, context?: SkillRequestContext) =>
    client.delete(`/api/skills/${name}`, { params: skillRequestParams(context) }),

  refresh: () =>
    client.post('/api/skills/refresh'),

  /**
   * Install a skill from an external source.
   *
   * Supported sources:
   *   clawhub:<name>        – clawhub.com registry
   *   github:<owner>/<repo> – GitHub repo (or shorthand owner/repo)
   *   https://...           – direct URL to SKILL.md
   *   /local/path           – local filesystem
   *   safeskill:<name>      – SafeSkill registry (future)
   */
  install: (req: SkillInstallRequest, context?: SkillRequestContext) =>
    client.post<SkillInstallResponse>('/api/skills/install', req, { params: skillRequestParams(context) }),

  /**
   * Install a skill's declared tool dependencies (brew, npm, uv, pip …).
   *
   * @param name       Skill name
   * @param installId  Optional: only run the spec with this id
   * @param timeoutMs  Subprocess timeout in ms (default 300000)
   */
  installDeps: (name: string, installId?: string, timeoutMs?: number, context?: SkillRequestContext) =>
    client.post<DepInstallResponse>(`/api/skills/${name}/install-deps`, {
      install_id: installId,
      timeout_ms: timeoutMs,
    }, { params: skillRequestParams(context) }),
};

export const commandAPI = {
  list: () =>
    client.get<Command[]>('/api/commands'),

  get: (name: string) =>
    client.get<Command>(`/api/commands/${name}`),
};
