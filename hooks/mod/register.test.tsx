// Run: claude plugin test <plugin folder>
// Every hook registered here stands for the engine or the host beneath the plugin: the Python side is the mocked
// `process.run`, whose argument vectors the tests read back.

import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

const SURFACES = ['terminal', 'desktop'] as const

const PANE = {
  plugin: 'docket',
  component: 'Pane',
  requestId: 'docket',
  props: {
    title: 'docket',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

const BAND = {
  plugin: 'docket',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isCollapsed: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 80,
    scroll: { offset: 0, bodyRows: 10 },
    view: {},
  },
} as const

const spec = (id: string, title: string, state: string, more: Record<string, unknown> = {}) => ({
  id,
  title,
  topic: 'core',
  state,
  concept: '',
  follows: [],
  created: '2026-11-01',
  path: `/work/specs/core/${id}.md`,
  docket: '',
  order: 0,
  points: null,
  result: null,
  decisions: 0,
  waits_for: [],
  running: '',
  superseded_by: '',
  ...more,
})

const overview = (more: Record<string, unknown> = {}) => ({
  ok: true,
  initialized: true,
  root: '/project',
  work: '/work',
  language: 'en',
  config: '/work/DOCKET.md',
  log_path: '/work/LOG.md',
  counts: { draft: 1, ready: 2, 'in-progress': 0, done: 1, superseded: 0, open: 3, total: 4 },
  concepts: [
    { id: 'c-11112222', title: 'Accounts', topic: 'core', state: 'agreed', path: '/work/c.md', decisions: 1 },
  ],
  specs: [
    spec('s-0123abcd', 'Login', 'ready', { concept: 'c-11112222', order: 0 }),
    spec('s-aaaa1111', 'Logout', 'ready', {
      concept: 'c-11112222',
      follows: ['s-0123abcd'],
      waits_for: ['s-0123abcd'],
      order: 1,
    }),
    spec('s-bbbb2222', 'Profile', 'draft', { decisions: 2, order: 2 }),
    spec('s-cccc3333', 'Signup', 'done', {
      docket: '/work/dockets/core/s-cccc3333.md',
      result: { kind: 'delivered', text: 'Signup works.' },
      order: 3,
    }),
  ],
  runs: [],
  log: [
    '2026-11-06T10:00:00Z  s-cccc3333  sealed             session=abcd1234',
    '2026-11-06T11:30:00Z  s-cccc3333  delivered          session=abcd1234  3/3',
  ],
  dirty: false,
  ...more,
})

const world = (on: On, read: () => unknown, tool: () => unknown = () => ({ result: {} })) => {
  const seen = {
    argv: [] as string[][],
    commands: [] as string[],
    prompts: [] as string[],
    tools: [] as Record<string, unknown>[],
    status: [] as (string | undefined)[],
    toasts: [] as string[],
    opened: [] as string[],
    store: new Map<string, unknown>(),
    listed: ['docket:implement', 'docket:stop', 'docket:init'],
  }
  const clock = mock.clock(on, { now: Date.UTC(2026, 10, 10, 12, 0) })

  on('store.get', (_, e) => ({ value: seen.store.get(e.key) }))
  on('store.set', (_, e) => {
    seen.store.set(e.key, e.value)

    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.root', () => ({ value: '/project' }))
  on('session.id', () => ({ value: 'session-1' }))
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('ui.open', (_, e) => {
    seen.opened.push(e.id)

    return { value: { isPlaced: true } }
  })
  on('ui.status', (_, e) => {
    seen.status.push(e.text)

    return { value: undefined }
  })
  on('ui.toast', (_, e) => {
    seen.toasts.push(e.text)

    return { value: undefined }
  })
  on('ui.notify', () => ({ value: { isSent: false, reason: 'no-surface' } }))
  on('ui.invalidate', () => ({ value: undefined }))
  on('fs.read', (_, e) => ({
    value: e.path.endsWith('.svg')
      ? '<svg xmlns="http://www.w3.org/2000/svg" width="203.1" height="52" viewBox="0 0 203.1 52"><g transform="scale(1)"><path fill="#E6EDF3" d="M0 0H9V9H0Z"/></g></svg>'
      : `---\nid: x\n---\n# Body of ${e.path}\n`,
  }))
  on('process.run', (_, e) => {
    seen.argv.push([...e.argv])
    const reply = e.argv[2] === 'overview' ? read() : { ok: true }

    return {
      value: {
        exitCode: 0,
        stdout: `${JSON.stringify(reply)}\n`,
        stderr: '',
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    }
  })
  on('command.list', () => ({
    value: seen.listed.map(name => ({ name, description: '', source: 'plugin' as const })),
  }))
  on('command.run', (_, e) => {
    seen.commands.push(`/${e.command} ${e.args}`.trim())

    return { text: '' }
  })
  on('prompt.submit', (_, e) => {
    seen.prompts.push(e.text)

    return { text: e.text }
  })
  on('tool.call', (_, e) => {
    seen.tools.push({ ...e })

    return tool() as { result: unknown }
  })

  return { seen, clock }
}

const start = async ($: Engine, clock: { settle: () => Promise<void> }) => {
  await $.session.start({ cwd: '/project', surface: 'desktop', isInteractive: true })
  await clock.settle()
}

test('a docket project opens the pane, sets the status line and lists specs under their concept', async ($, on) => {
  const { seen, clock } = world(on, overview)

  await start($, clock)
  expect(seen.opened).toEqual(['docket'])
  expect(seen.status.at(-1)).toBe('docket: 3 open (1 draft · 2 ready · 0 in progress) · 1 done · 0 superseded')
  expect(seen.argv[0]?.slice(1)).toEqual([
    expect.stringContaining('/hooks/docket_ui.py'),
    'overview',
    '--root',
    '/project',
    '--session',
    'session-1',
  ])

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })

    expect((await ui.find({ type: 'Text', text: /^Accounts$/ }))?.text).toBe('Accounts')
    expect((await ui.find({ key: 'spec-s-aaaa1111' }))?.text).toContain('Waits for: Login')
    expect((await ui.find({ key: 'spec-s-bbbb2222' }))?.text).toContain('2 open decisions')
    expect(await ui.find({ type: 'Text', text: /Without a concept/ })).toBeDefined()
    expect((await ui.find({ key: 'all-c-11112222' }))?.text).toBe('Implement all ready (2)')
    expect(await ui.find({ type: 'Text', text: /Done 1/ })).toBeDefined()

    // The head: the logo where the surface draws vectors, the name as text on the terminal.
    const logo = await ui.find({ type: 'Svg' })

    if (surface === 'desktop') {
      expect(logo?.props.alt).toBe('docket')
      expect(String(logo?.props.source)).toContain('class="f" fill="#8B949E"')
      expect(String(logo?.props.source)).toContain('prefers-color-scheme:dark')
    } else {
      expect(logo).toBeUndefined()
      expect(await ui.find({ type: 'Text', text: /^docket$/ })).toBeDefined()
    }

    await ui.unmount()
  }
})

test('a spec opens to its text, and its button starts the run in this session', async ($, on) => {
  const { seen, clock } = world(on, overview)

  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  await ui.press({ key: 'spec-s-0123abcd' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Markdown' }))?.text).toMatch(/^# Body of .*s-0123abcd.md/)
  expect((await ui.find({ type: 'Markdown' }))?.text).not.toContain('id: x')
  await ui.press({ key: 'implement' })
  expect(seen.commands).toEqual(['/docket:implement s-0123abcd'])
  await ui.press({ key: 'back' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'all-c-11112222' })
  expect(seen.commands.at(-1)).toBe('/docket:implement c-11112222')

  // A session that does not list the command gets the same line as a prompt, which the hooks read as well.
  seen.listed = []
  await ui.press({ key: 'all-c-11112222' })
  expect(seen.commands.length).toBe(2)
  expect(seen.prompts).toEqual(['From the docket pane, on my button:\n/docket:implement c-11112222'])
  await ui.unmount()
})

test('a session of its own is started through the session tool, else Claude is asked for it', async ($, on) => {
  let answer: unknown = { result: {} }
  const { seen, clock } = world(on, overview, () => answer)

  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  await ui.press({ key: 'spec-s-0123abcd' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'implement-session' })

  const sent = seen.tools.find(call => call.tool === 'mcp__ccd_session__start_session')

  expect(sent?.initiation).toBe('user_asked')
  expect(String(sent?.prompt).split('\n').at(-1)).toBe('/docket:implement s-0123abcd')
  expect(String(sent?.background).length).toBeGreaterThan(80)
  expect(seen.prompts).toEqual([])

  answer = { deny: 'not allowed' }
  await ui.press({ key: 'implement-session' })
  expect(seen.prompts.at(-1)).toContain('\n/docket:implement s-0123abcd')
  await ui.unmount()
})

test('several ready specs are ticked and run as one series, in implementation order', async ($, on) => {
  const { seen, clock } = world(on, overview)
  const remount = async (ui: { unmount: () => Promise<void> }) => {
    await ui.unmount()

    return $.ui.mount({ ...PANE, surface: 'desktop' })
  }

  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  // Only a ready spec has a tick; nothing is selected yet.
  expect(await ui.find({ key: 'pick-s-bbbb2222' })).toBeUndefined()
  expect(await ui.find({ key: 'picked-implement' })).toBeUndefined()

  // Logout alone waits for Login: the pane says so and offers no run the hooks would refuse.
  await ui.press({ key: 'pick-s-aaaa1111' })
  ui = await remount(ui)
  expect((await ui.find({ key: 'pick-s-aaaa1111' }))?.text).toBe('☑')
  expect(await ui.find({ type: 'Text', text: /waits for .Login.: select that one too/ })).toBeDefined()
  expect(await ui.find({ key: 'picked-implement' })).toBeUndefined()

  // Ticked second, run first: the series goes in implementation order.
  await ui.press({ key: 'pick-s-0123abcd' })
  ui = await remount(ui)
  expect(await ui.find({ type: 'Text', text: /2 selected, in run order: Login, Logout/ })).toBeDefined()
  expect((await ui.find({ key: 'picked-implement' }))?.text).toBe('Implement selection (2)')
  await ui.press({ key: 'picked-implement' })
  expect(seen.commands).toEqual(['/docket:implement s-0123abcd, s-aaaa1111'])

  // The run took the selection with it.
  ui = await remount(ui)
  expect(await ui.find({ key: 'picked-implement' })).toBeUndefined()

  await ui.press({ key: 'pick-s-0123abcd' })
  await ui.press({ key: 'pick-s-aaaa1111' })
  ui = await remount(ui)
  await ui.press({ key: 'picked-session' })
  expect(String(seen.tools.at(-1)?.prompt)).toMatch(/\/docket:implement s-0123abcd, s-aaaa1111$/)

  await ui.press({ key: 'pick-s-0123abcd' })
  ui = await remount(ui)
  await ui.press({ key: 'picked-clear' })
  ui = await remount(ui)
  expect((await ui.find({ key: 'pick-s-0123abcd' }))?.text).toBe('☐')
  await ui.unmount()
})

test('superseded is set with the spec that replaces it, a successor first in the list', async ($, on) => {
  const { seen, clock } = world(on, overview)

  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  await ui.press({ key: 'spec-s-0123abcd' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'supersede' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  const options = (await ui.find({ key: 'supersede-by' }))?.props.options as { value: string }[]

  expect(options.map(option => option.value)).toEqual(['s-aaaa1111', 's-bbbb2222', 's-cccc3333'])
  await ui.select({ key: 'supersede-by', value: 's-bbbb2222' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'supersede-confirm' })
  expect(seen.argv.find(argv => argv[2] === 'supersede')?.slice(-4)).toEqual([
    '--spec',
    's-0123abcd',
    '--by',
    's-bbbb2222',
  ])
  await ui.unmount()
})

test('a run shows with its progress; this session stops by command, another by its session id', async ($, on) => {
  const run = (session: string, mine: boolean) => ({
    session,
    mine,
    mode: 'sealed',
    spec: 's-0123abcd',
    title: 'Login',
    i: 1,
    n: 2,
    queue: ['s-aaaa1111'],
    stop_requested: false,
    points: { done: 1, deviating: 0, open: 2 },
  })
  let runs = [run('session-1', true), run('other-session-9', false)]
  const { seen, clock } = world(on, () => overview({ runs }))

  await start($, clock)
  expect(seen.status.at(-1)).toContain('▶ Login 1/3 · docket: 3 open')

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  expect(await ui.find({ type: 'Text', text: /spec 1 of 2 · 1 of 3 points/ })).toBeDefined()
  await ui.press({ key: 'stop-session-1' })
  expect(seen.commands).toEqual(['/docket:stop'])
  await ui.press({ key: 'stop-other-session-9' })
  expect(seen.argv.find(argv => argv[2] === 'stop')?.slice(-2)).toEqual(['--session', 'other-session-9'])
  await ui.unmount()

  // The run is gone at the next reading: the person is told once how it ended.
  runs = []
  await clock.advance(20000)
  expect(seen.toasts.at(-1)).toContain('Login')
})

test('the LOG and DOCKET.md have their tabs', async ($, on) => {
  const { clock } = world(on, overview)

  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  await ui.press({ key: 'tab-log' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Markdown' }))?.text).toContain('| 11-06 11:30 | Signup | delivered | 3/3 |')
  // A done spec's docket is one click away from the register.
  await ui.press({ key: 'tab-register' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ key: 'docket-s-0123abcd' })).toBeUndefined()
  await ui.press({ key: 'docket-s-cccc3333' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Markdown' }))?.text).toMatch(/^# Body of .*dockets.*s-cccc3333.md/)
  await ui.press({ key: 'tab-config' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Markdown' }))?.text).toMatch(/^# Body of .*DOCKET.md/)
  await ui.unmount()
})

test('a project without docket gets a band that starts the setup and can be hidden for good', async ($, on) => {
  const { seen, clock } = world(on, () => ({ ok: true, initialized: false, root: '/project', work: '/w' }))

  // Once the band is hidden the plugin leaves the row to the engine: this hook stands for its drawing.
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)

    return <Text>engine</Text>
  })

  await start($, clock)
  expect(seen.opened).toEqual([])
  expect(seen.status.at(-1)).toBeUndefined()

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...BAND, surface })

    expect((await ui.find({ key: 'docket-init' }))?.text).toBe('Set up docket')
    await ui.unmount()
  }

  let ui = await $.ui.mount({ ...BAND, surface: 'desktop' })

  await ui.press({ key: 'docket-init' })
  expect(seen.commands).toEqual(['/docket:init'])
  await ui.press({ key: 'docket-hide' })
  await ui.unmount()
  ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect(await ui.find({ key: 'docket-init' })).toBeUndefined()
  expect(seen.store.get('band-hidden')).toEqual(['/project'])
  await ui.unmount()
})
