// The pane's one way to the project: hooks/docket_ui.py, which reads and writes with the hooks' own functions.

import type { EngineInterface } from 'claude-code'

export type Points = { done: number; deviating: number; open: number }

export type Spec = {
  id: string
  title: string
  topic: string
  state: string
  concept: string
  follows: string[]
  created: string
  path: string
  docket: string
  order: number
  points: Points | null
  result: { kind: string; text: string } | null
  decisions: number
  waits_for: string[]
  running: string
  superseded_by: string
}

export type Concept = {
  id: string
  title: string
  topic: string
  state: string
  path: string
  decisions: number
}

export type Run = {
  session: string
  mine: boolean
  mode: string
  spec: string
  title: string
  i: number
  n: number
  queue: string[]
  stop_requested: boolean
  points: Points | null
}

export type Overview = {
  ok: true
  initialized: boolean
  root: string
  work: string
  language?: string
  config?: string
  log_path?: string
  counts?: Record<string, number>
  concepts?: Concept[]
  specs?: Spec[]
  runs?: Run[]
  log?: string[]
  dirty?: boolean
}

export type Failure = { ok: false; error: string; detail?: string }

export type Reply<T> = T | Failure

type Extra = { session?: string; spec?: string; by?: string; command?: string }

const PYTHONS = ['python', 'python3', 'py']
const FRONTMATTER = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/
const BYTE_ORDER_MARK = 0xfeff
let python: string | undefined

/** Runs one action of docket_ui.py and answers its JSON; `{ ok: false, error: 'python' }` when no Python starts. */
export async function call<T>(
  $: EngineInterface,
  action: 'overview' | 'supersede' | 'stop' | 'handoff',
  extra: Extra = {},
): Promise<Reply<T>> {
  const args = [
    `${$.plugin.root}/hooks/docket_ui.py`,
    action,
    '--root',
    await $.session.root(),
    '--session',
    extra.session ?? (await $.session.id()),
  ]

  if (extra.spec) {
    args.push('--spec', extra.spec)
  }

  if (extra.by) {
    args.push('--by', extra.by)
  }

  if (extra.command) {
    args.push('--command', extra.command)
  }

  for (const candidate of python ? [python] : PYTHONS) {
    try {
      const ran = await $.process.run([candidate, ...args], { timeoutMs: 20000 })
      // The answer is the last line: a Python that is only a launcher stub prints none, and the next one is tried.
      const parsed: unknown = JSON.parse(ran.stdout.trim().split('\n').pop() ?? '')

      if (typeof parsed === 'object' && parsed !== null && 'ok' in parsed) {
        python = candidate

        return parsed as Reply<T>
      }
    } catch {
      // not this one
    }
  }

  return { ok: false, error: 'python' }
}

/** A markdown file without its frontmatter, or undefined when it cannot be read. */
export async function readBody($: EngineInterface, path: string): Promise<string | undefined> {
  try {
    const text = await $.fs.read(path)
    const body = text.charCodeAt(0) === BYTE_ORDER_MARK ? text.slice(1) : text

    return body.replace(FRONTMATTER, '')
  } catch {
    return undefined
  }
}
