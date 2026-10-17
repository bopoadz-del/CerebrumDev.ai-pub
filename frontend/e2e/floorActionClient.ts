import { readFileSync } from 'node:fs'

/**
 * The browser e2e's typed Floor action client. It reads the SAME committed
 * spec as the SPA and scripts/post_deploy_smoke.py
 * (src/api/floor_actions.json, exported from the backend's
 * app.factory.floor_actions; backend test_floor_action_spec fails on drift),
 * so a mocked route can only expect an action the backend defines.
 */

type ValueShape = null | 'string' | { one_of: string[] }

interface FloorActionSpec {
  schema: string
  actions: Record<string, { value: ValueShape }>
}

export const FLOOR_ACTIONS: FloorActionSpec['actions'] = (
  JSON.parse(
    readFileSync(new URL('../src/api/floor_actions.json', import.meta.url), 'utf-8'),
  ) as FloorActionSpec
).actions

export interface TypedAction {
  action: string
  value?: string
}

/** A typed action as the SPA sends it; refuses anything the spec lacks. */
export function typedAction(action: string, value?: string): TypedAction {
  const rule = FLOOR_ACTIONS[action]
  if (!rule) throw new Error(`undefined Floor action ${JSON.stringify(action)}`)
  if (rule.value === null) {
    if (value !== undefined) throw new Error(`Floor action ${action} takes no value`)
    return { action }
  }
  if (!value) throw new Error(`Floor action ${action} needs a value`)
  if (typeof rule.value === 'object' && !rule.value.one_of.includes(value)) {
    throw new Error(`Floor action ${action} value must be one of ${rule.value.one_of.join(', ')}`)
  }
  return { action, value }
}

/** The typed action a posted chat body carries (null for free text). */
export function postedAction(body: { action?: string; value?: string }): TypedAction | null {
  if (!body.action) return null
  return typedAction(body.action, body.value)
}
