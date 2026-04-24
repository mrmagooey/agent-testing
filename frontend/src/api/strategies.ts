// TypeScript types for the Strategies API

export type OrchestrationShape =
  | 'single_agent'
  | 'per_file'
  | 'per_vuln_class'
  | 'sast_first'
  | 'diff_review'

export interface StrategyBundleDefault {
  system_prompt: string
  user_prompt_template: string
  profile_modifier: string
  model_id: string
  tools: string[]
  verification: string
  max_turns: number
  tool_extensions: string[]
}

export interface StrategyBundleOverride {
  system_prompt?: string | null
  user_prompt_template?: string | null
  profile_modifier?: string | null
  model_id?: string | null
  tools?: string[] | null
  verification?: string | null
  max_turns?: number | null
  tool_extensions?: string[] | null
}

export interface OverrideRule {
  key: string
  override: StrategyBundleOverride
}

export interface StrategySummary {
  id: string
  name: string
  orchestration_shape: OrchestrationShape
  is_builtin: boolean
  parent_strategy_id: string | null
}

export interface UserStrategy {
  id: string
  name: string
  parent_strategy_id: string | null
  orchestration_shape: OrchestrationShape
  default: StrategyBundleDefault
  overrides: OverrideRule[]
  created_at: string
  is_builtin: boolean
}

export interface StrategyCreateBody {
  parent_strategy_id?: string | null
  name: string
  default: StrategyBundleDefault
  overrides: OverrideRule[]
  orchestration_shape: OrchestrationShape
}

export interface StrategyValidateBody {
  default?: Partial<StrategyBundleDefault> | null
  overrides?: OverrideRule[] | null
  orchestration_shape?: OrchestrationShape | null
}

export interface StrategyValidateResult {
  valid: boolean
  errors: string[]
}

// Standard vuln classes (from VulnClass enum in findings.py)
export const VULN_CLASSES = [
  'sqli',
  'xss',
  'ssrf',
  'rce',
  'idor',
  'auth_bypass',
  'crypto_misuse',
  'hardcoded_secret',
  'path_traversal',
  'supply_chain',
  'memory_safety',
  'logic_bug',
  'deserialization',
  'xxe',
  'open_redirect',
  'other',
] as const

export type VulnClassName = (typeof VULN_CLASSES)[number]

// Sample files used for glob preview in per_file/sast_first editors
export const GLOB_PREVIEW_SAMPLE_FILES = [
  'src/auth/login.py',
  'src/auth/oauth.py',
  'src/api/endpoints.py',
  'src/db/queries.py',
  'src/utils/crypto.py',
  'tests/test_auth.py',
  'tests/test_api.py',
  'frontend/src/App.tsx',
  'frontend/src/components/Login.tsx',
  'README.md',
]
