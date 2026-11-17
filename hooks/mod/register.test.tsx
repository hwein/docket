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

const MODES = {
  plugin: 'docket',
  component: 'SessionMode',
  props: { modes: [] as string[] },
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
  closed: '',
  path: `/work/specs/core/${id}.md`,
  docket: '',
  order: 0,
  points: null,
  result: null,
  decisions: 0,
  waits_for: [],
  preconditions: { lines: 0, checked: '', met: null, detail: '' },
  held: false,
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
    {
      id: 'c-11112222',
      title: 'Accounts',
      topic: 'core',
      state: 'agreed',
      path: '/work/c.md',
      source: '',
      decisions: 1,
    },
  ],
  unregistered: [],
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
    root: '/project',
    draft: '',
    canFill: false,
    filled: [] as string[],
  }
  const clock = mock.clock(on, { now: Date.UTC(2026, 10, 10, 12, 0) })

  on('store.get', (_, e) => ({ value: seen.store.get(e.key) }))
  on('store.set', (_, e) => {
    seen.store.set(e.key, e.value)

    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('classic.SessionStart', () => ({}))
  on('session.root', () => ({ value: seen.root }))
  on('prompt.read', () => ({ value: { text: seen.draft, cursor: 0 } }))
  on('prompt.fill', (_, e) => {
    if (!seen.canFill) {
      return { isFilled: false }
    }

    seen.filled.push(e.text)

    return { isFilled: true }
  })
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
  // The engine's drawing of the footer's mode labels, beneath the plugin's hook.
  on('ui.render', { component: 'SessionMode' }, ($, e) => {
    const { Text } = $.ui.resolve(e)

    return <Text>{`engine: ${e.props.modes.join(' & ')}`}</Text>
  })
  on('ui.toast', (_, e) => {
    seen.toasts.push(e.text)

    return { value: undefined }
  })
  on('ui.notify', () => ({ value: { isSent: false, reason: 'no-surface' } }))
  on('ui.invalidate', () => ({ value: undefined }))
  on('fs.read', (_, e) => ({
    value: /[\\/]\.claude-plugin[\\/]plugin\.json$/.test(e.path)
      ? '{"name": "docket", "version": "1.2.3"}'
      : e.path.endsWith('.svg')
      ? '<svg xmlns="http://www.w3.org/2000/svg" width="203.1" height="52" viewBox="0 0 203.1 52"><g transform="scale(1)"><path fill="#E6EDF3" d="M0 0H9V9H0Z"/></g></svg>'
      : `---\nid: x\n---\n# Heading of the file\n\nBody of ${e.path}\n`,
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
  expect(seen.status.at(-1)).toBe('1.2.3')
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
    // A row is position, state glyph and title; what holds the spec stands beside it, the predecessor by its
    // position (luradb-web-client 2026-11-10: the state word and the predecessor's title in every row).
    expect((await ui.find({ key: 'spec-s-aaaa1111' }))?.text).toBe('2. ● Logout')
    expect(await ui.find({ type: 'Text', text: /^Waits for #1$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^2 open decisions$/ })).toBeDefined()
    // The finished specs stand in a table of their own, below the groups.
    expect(await ui.find({ type: 'Text', text: /^Finished \(1\)$/ })).toBeDefined()
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

/** What a spec's row says in front of its state glyph. */
const lead = (row: { text: string } | undefined): string | undefined =>
  row?.text.slice(0, row.text.search(/[○●◐✔⊘]/))

test('only a spec in a dependency carries a number, counted across concepts', async ($, on) => {
  const { clock } = world(on, () =>
    overview({
      concepts: [
        { id: 'c-11112222', title: 'Accounts', topic: 'core', state: 'agreed', path: '/work/c.md', decisions: 0 },
        { id: 'c-33334444', title: 'Billing', topic: 'core', state: 'agreed', path: '/work/d.md', decisions: 0 },
      ],
      specs: [
        // The older of the two, and ready where Login is a draft, yet after it: it follows Login.
        spec('s-aaaa1111', 'Logout', 'ready', {
          concept: 'c-11112222',
          created: '2026-11-01',
          follows: ['s-0123abcd'],
          waits_for: ['s-0123abcd'],
          order: 4,
        }),
        spec('s-0123abcd', 'Login', 'draft', { concept: 'c-11112222', created: '2026-11-03', order: 3 }),
        spec('s-eeee5555', 'Tariff', 'in-progress', { concept: 'c-33334444', created: '2026-11-04', order: 5 }),
        spec('s-dddd4444', 'Invoice', 'ready', { concept: 'c-33334444', created: '2026-11-02', order: 2 }),
        // Written first and closed last: it leads the finished rows.
        spec('s-ffff6666', 'Guest', 'superseded', {
          concept: 'c-11112222',
          created: '2026-10-19',
          closed: '2026-11-09T08:00:00Z',
          order: 1,
        }),
        spec('s-cccc3333', 'Signup', 'done', {
          concept: 'c-11112222',
          created: '2026-10-20',
          closed: '2026-11-07T16:00:00Z',
          order: 0,
        }),
        // No line of the LOG closed it: the day it was written stands for that.
        spec('s-9999eeee', 'Reset', 'done', { concept: 'c-11112222', created: '2026-10-22', order: 6 }),
      ],
    }),
  )

  await start($, clock)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const rows = (await ui.findAll({ type: 'Button' })).filter(found => found.key?.startsWith('spec-'))

    // Open specs under their concept, a predecessor before what waits for it; the finished ones of every concept in
    // the table below, the one closed last first.
    expect(rows.map(row => row.key)).toEqual([
      'spec-s-0123abcd',
      'spec-s-aaaa1111',
      'spec-s-dddd4444',
      'spec-s-eeee5555',
      'spec-s-ffff6666',
      'spec-s-cccc3333',
      'spec-s-9999eeee',
    ])
    // Login and Logout are tied by `follows:` and numbered; Invoice and Tariff wait for nothing and nothing waits for
    // them: a blank of a number's width.
    expect(rows.slice(0, 4).map(lead)).toEqual(['1. ', '2. ', '\u2007\u2008 ', '\u2007\u2008 '])
    // A finished row's Button is the title alone: position it has none, and its state glyph stands beside it.
    expect(rows.slice(4).map(row => row.text)).toEqual(['Guest', 'Signup', 'Reset'])
    expect((await ui.find({ type: 'Text', text: /Waits for #1/ }))?.text).toBe('Waits for #1')
    await ui.unmount()
  }
})

test('the numbers are explained where a dependency is, and ten of them stay in one column', async ($, on) => {
  const done = spec('s-cccc3333', 'Signup', 'done', { order: 0 })
  let specs = [done, spec('s-0123abcd', 'Login', 'ready', { order: 1 })]
  const chain = (i: number) =>
    spec(`s-0000000${i}`, `Step ${i}`, 'draft', { order: i + 1, waits_for: i === 0 ? [] : [`s-0000000${i - 1}`] })
  let language = 'en'
  const { clock } = world(on, () => overview({ concepts: [], specs, language }))
  const reread = async (ui: { press: (target: { key: string }) => Promise<unknown>; unmount: () => Promise<void> }) => {
    await ui.press({ key: 'refresh' })
    await ui.unmount()

    return $.ui.mount({ ...PANE, surface: 'desktop' })
  }

  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  // One open spec: no number and no column.
  expect(lead(await ui.find({ key: 'spec-s-0123abcd' }))).toBe('')
  expect(await ui.find({ type: 'Text', text: /any order/ })).toBeUndefined()

  // Two open specs that nothing ties: the specs say no order, and the pane states none.
  specs = [...specs, spec('s-aaaa1111', 'Logout', 'draft', { order: 2 })]
  ui = await reread(ui)
  expect(lead(await ui.find({ key: 'spec-s-0123abcd' }))).toBe('')
  expect(lead(await ui.find({ key: 'spec-s-aaaa1111' }))).toBe('')
  expect(await ui.find({ type: 'Text', text: /any order/ })).toBeUndefined()

  // Logout waits for Login: both carry a number, a third spec beside them a blank of that width.
  specs = [
    done,
    spec('s-0123abcd', 'Login', 'ready', { order: 1 }),
    spec('s-aaaa1111', 'Logout', 'draft', { order: 2, waits_for: ['s-0123abcd'] }),
    spec('s-bbbb2222', 'Profile', 'ready', { order: 3 }),
  ]
  ui = await reread(ui)
  expect(lead(await ui.find({ key: 'spec-s-0123abcd' }))).toBe('1. ')
  expect(lead(await ui.find({ key: 'spec-s-aaaa1111' }))).toBe('2. ')
  expect(lead(await ui.find({ key: 'spec-s-bbbb2222' }))).toBe('   ')
  expect((await ui.find({ type: 'Text', text: /any order/ }))?.text).toBe(
    'A number marks a spec in a dependency: “Waits for #n” names the spec with that number. Specs without a number can be taken in any order.',
  )

  language = 'de'
  ui = await reread(ui)
  expect((await ui.find({ type: 'Text', text: /beliebiger Reihenfolge/ }))?.text).toBe(
    'Eine Zahl kennzeichnet eine Spec in einer Abhängigkeit: „Wartet auf #n“ nennt die Spec mit dieser Zahl. Specs ohne Zahl sind in beliebiger Reihenfolge wählbar.',
  )

  // Two digits: the shorter numbers take the width of the longest. The finished spec stands in the table, where no
  // row has a position.
  specs = [done, ...Array.from({ length: 10 }, (_, i) => chain(i))]
  ui = await reread(ui)
  expect(lead(await ui.find({ key: 'spec-s-00000000' }))).toBe('\u20071. ')
  expect(lead(await ui.find({ key: 'spec-s-00000009' }))).toBe('10. ')
  expect((await ui.find({ key: 'spec-s-cccc3333' }))?.text).toBe('Signup')
  // The table speaks the project's language.
  expect(await ui.find({ type: 'Text', text: /^Abgeschlossen \(1\)$/ })).toBeDefined()
  expect((await ui.find({ key: 'sort-closed' }))?.text).toBe('Abschluss ▼')
  expect((await ui.find({ key: 'sort-title' }))?.text).toBe('Titel')

  // No open spec: the table alone, and no hint about numbers.
  specs = [done]
  ui = await reread(ui)
  expect((await ui.find({ key: 'spec-s-cccc3333' }))?.text).toBe('Signup')
  expect(await ui.find({ type: 'Text', text: /beliebiger Reihenfolge/ })).toBeUndefined()
  await ui.unmount()
})

test('documents in the concept folders that no concept links are counted in one dim line', async ($, on) => {
  let unregistered: string[] | undefined = ['docs/rfcs/0007-sync.md']
  let language = 'en'
  const { clock } = world(on, () =>
    overview({
      concepts: [
        {
          id: 'c-11112222',
          title: 'Accounts',
          topic: 'core',
          state: 'agreed',
          path: '/work/c.md',
          source: 'docs/rfcs/0003-accounts.md',
          decisions: 0,
        },
      ],
      unregistered,
      language,
    }),
  )
  const reread = async (ui: { press: (target: { key: string }) => Promise<unknown>; unmount: () => Promise<void> }) => {
    await ui.press({ key: 'refresh' })
    await ui.unmount()

    return $.ui.mount({ ...PANE, surface: 'desktop' })
  }
  const line = /concept folders|Konzeptordnern/

  await start($, clock)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })

    expect((await ui.find({ type: 'Text', text: line }))?.text).toBe(
      '1 document in the concept folders is not registered: /docket:concept',
    )
    await ui.unmount()
  }

  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  unregistered = ['docs/rfcs/0007-sync.md', 'docs/rfcs/0008-export.md', 'docs/rfcs/0009-import.md']
  ui = await reread(ui)
  expect((await ui.find({ type: 'Text', text: line }))?.text).toBe(
    '3 documents in the concept folders are not registered: /docket:concept',
  )

  language = 'de'
  ui = await reread(ui)
  expect((await ui.find({ type: 'Text', text: line }))?.text).toBe(
    '3 Dokumente in den Konzeptordnern sind nicht erfasst: /docket:concept',
  )

  unregistered = ['docs/rfcs/0007-sync.md']
  ui = await reread(ui)
  expect((await ui.find({ type: 'Text', text: line }))?.text).toBe(
    '1 Dokument in den Konzeptordnern ist nicht erfasst: /docket:concept',
  )

  // Nothing unregistered, and a docket_ui.py of 0.6.6 that does not send the key: no line.
  unregistered = []
  ui = await reread(ui)
  expect(await ui.find({ type: 'Text', text: line })).toBeUndefined()

  unregistered = undefined
  ui = await reread(ui)
  expect(await ui.find({ type: 'Text', text: line })).toBeUndefined()
  await ui.unmount()
})

test('a spec opens to its text, and its button starts the run in this session', async ($, on) => {
  const { seen, clock } = world(on, overview)

  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  await ui.press({ key: 'spec-s-0123abcd' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Markdown' }))?.text).toMatch(/^Body of .*s-0123abcd.md/)
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

test('a spec whose precondition check says not met starts no run, and its button has them checked again', async ($, on) => {
  let check: Record<string, unknown> = {
    lines: 1,
    checked: '2026-11-17',
    met: false,
    detail: '`curl localhost:8080` gave no answer',
  }
  const { seen, clock } = world(on, () =>
    overview({
      specs: [
        spec('s-0123abcd', 'Login', 'ready', { concept: 'c-11112222', order: 0, preconditions: check, held: check.met === false }),
        // Held with the spec it follows.
        spec('s-aaaa1111', 'Logout', 'ready', {
          concept: 'c-11112222',
          follows: ['s-0123abcd'],
          waits_for: ['s-0123abcd'],
          order: 1,
          held: check.met === false,
        }),
        spec('s-bbbb2222', 'Profile', 'ready', { concept: 'c-11112222', order: 2 }),
      ],
    }),
  )

  seen.listed.push('docket:precheck')
  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  // The row says what holds it; neither held spec can be ticked, and the concept's run counts the free one alone.
  expect(await ui.find({ type: 'Text', text: /^Waits for a precondition$/ })).toBeDefined()
  expect(await ui.find({ key: 'pick-s-0123abcd' })).toBeUndefined()
  expect(await ui.find({ key: 'pick-s-aaaa1111' })).toBeUndefined()
  expect(await ui.find({ key: 'pick-s-bbbb2222' })).toBeDefined()
  expect((await ui.find({ key: 'all-c-11112222' }))?.text).toBe('Implement all ready (1)')

  await ui.press({ key: 'spec-s-0123abcd' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(
    await ui.find({ type: 'Text', text: /^not met \(checked 2026-11-17\): `curl localhost:8080` gave no answer$/ }),
  ).toBeDefined()
  expect(await ui.find({ key: 'implement' })).toBeUndefined()
  await ui.press({ key: 'precheck' })
  expect(seen.commands).toEqual(['/docket:precheck s-0123abcd'])

  // The check run again says met: the spec can run, and the button stays for the next check.
  check = { lines: 1, checked: '2026-11-18', met: true, detail: '' }
  await ui.press({ key: 'refresh' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /^met \(checked 2026-11-18\)$/ })).toBeDefined()
  expect(await ui.find({ key: 'implement' })).toBeDefined()
  expect(await ui.find({ key: 'precheck' })).toBeDefined()

  // A spec without preconditions has nothing to check.
  await ui.press({ key: 'back' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'spec-s-bbbb2222' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ key: 'precheck' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^Preconditions$/ })).toBeUndefined()
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
  // The request names the command inside a sentence: on a line of its own the hooks would seal the asking session,
  // and the session it starts would find the spec taken (luradb-web-client 2026-11-10).
  expect(seen.prompts.at(-1)).toContain('`/docket:implement s-0123abcd`')
  expect(String(seen.prompts.at(-1)).split('\n').some(line => line.trim().startsWith('/'))).toBe(false)
  await ui.unmount()
})

test('where the session tool starts none, the request waits in the empty prompt box for the person to send', async ($, on) => {
  // luradb-web-client 2026-11-10: the app refused the session on the turn the pane's own prompt began, and started
  // it with the same call on the next turn the person typed.
  const { seen, clock } = world(on, overview, () => ({ deny: 'not allowed' }))

  seen.canFill = true
  await start($, clock)
  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  await ui.press({ key: 'spec-s-0123abcd' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'implement-session' })

  expect(seen.filled.at(-1)).toContain('`/docket:implement s-0123abcd`')
  expect(seen.prompts).toEqual([])
  // The session that is started finds its command by this record, wherever the app puts it in the brief.
  expect(seen.argv.find(argv => argv[2] === 'handoff')?.slice(-2)).toEqual([
    '--command',
    '/docket:implement s-0123abcd',
  ])

  // A draft of the person is never written over: Claude is asked instead.
  seen.draft = 'half a sentence'
  await ui.press({ key: 'implement-session' })
  expect(seen.filled.length).toBe(1)
  expect(seen.prompts.at(-1)).toContain('own_initiative')
  await ui.unmount()
})

test('a session the app moves to its project with the first prompt opens the pane on the session start hooks', async ($, on) => {
  // luradb-web-client 2026-11-10: the process of a new session stood in the app's placeholder folder until the first
  // prompt (`relocated` in the transcript); the pane read that folder once and came up after the first turn only.
  let reply: unknown = { ok: true, initialized: false, root: '/spare', work: '/spare/docket' }
  const { seen, clock } = world(on, () => reply)

  seen.root = '/spare'
  await $.session.start({ cwd: '/spare', surface: 'desktop', isInteractive: true })
  await clock.settle()
  expect(seen.opened).toEqual([])

  seen.root = '/project'
  reply = overview()
  await $.classic.SessionStart({ source: 'startup' })
  await clock.settle()
  expect(seen.opened).toEqual(['docket'])
  expect(seen.argv.at(-1)?.slice(2, 5)).toEqual(['overview', '--root', '/project'])

  // The same folder again reads nothing and opens nothing a second time.
  const reads = seen.argv.length

  await $.classic.SessionStart({ source: 'compact' })
  await clock.settle()
  expect(seen.argv.length).toBe(reads)
  expect(seen.opened).toEqual(['docket'])
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
  // A run stands in the pane, not in the status line.
  expect(seen.status.at(-1)).toBe('1.2.3')

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
  expect((await ui.find({ type: 'Markdown' }))?.text).toMatch(/^Body of .*dockets.*s-cccc3333.md/)
  await ui.press({ key: 'tab-config' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Markdown' }))?.text).toMatch(/^# Heading of the file/)
  await ui.unmount()
})

/** The keys of the finished table's rows in the order drawn: every spec row but the open ones named. */
const tableRows = async (
  ui: { findAll: (query: { type: string }) => Promise<{ key?: string }[]> },
  open: readonly string[] = [],
): Promise<string[]> =>
  (await ui.findAll({ type: 'Button' }))
    .map(found => found.key ?? '')
    .filter(key => key.startsWith('spec-') && !open.includes(key))

test('finished specs stand in one table: a green tick beside the title, heads that sort, a search of the titles', async ($, on) => {
  const specs = [
    spec('s-0123abcd', 'Login', 'ready', { concept: 'c-11112222', order: 0 }),
    spec('s-cccc3333', 'Signup', 'done', {
      concept: 'c-11112222',
      closed: '2026-11-07T16:00:00Z',
      docket: '/work/dockets/core/s-cccc3333.md',
      points: { done: 2, deviating: 1, open: 0 },
    }),
    spec('s-ffff6666', 'Guest login', 'superseded', { closed: '2026-11-09T08:00:00Z', superseded_by: 's-0123abcd' }),
    spec('s-9999eeee', 'Account reset', 'done', {
      closed: '2026-11-08T09:30:00Z',
      docket: '/work/dockets/core/s-9999eeee.md',
    }),
  ]
  const open = ['spec-s-0123abcd']
  let root = '/project'
  const { seen, clock } = world(on, () => overview({ specs, root }))

  await start($, clock)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })

    // The tick is a text of its own in the success color: the desktop app draws a Button in one color, so inside
    // the title's Button it was grey (2026-11-17).
    expect((await ui.find({ type: 'Text', text: /^✔$/ }))?.props.color).toBe('success')
    expect((await ui.find({ type: 'Text', text: /^⊘$/ }))?.props.color).toBe('inactive')
    expect((await ui.find({ key: 'spec-s-cccc3333' }))?.text).toBe('Signup')
    // One table for every concept and for the specs without one, the spec closed last first.
    expect(await ui.find({ type: 'Text', text: /^Finished \(3\)$/ })).toBeDefined()
    expect(await tableRows(ui, open)).toEqual(['spec-s-ffff6666', 'spec-s-9999eeee', 'spec-s-cccc3333'])
    expect((await ui.find({ key: 'sort-closed' }))?.text).toBe('Closed ▼')
    expect((await ui.find({ key: 'sort-title' }))?.text).toBe('Title')
    expect((await ui.find({ key: 'sort-concept' }))?.text).toBe('Concept')
    // A row keeps what the list said: the concept it stood under (the heading and the row name it), the note, the
    // docket of a done spec; the day closed is new.
    expect((await ui.findAll({ type: 'Text', text: /^Accounts$/ })).length).toBe(2)
    expect(await ui.find({ type: 'Text', text: /^2026-11-07$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^~1$/ })).toBeDefined()
    expect(await ui.find({ key: 'docket-s-cccc3333' })).toBeDefined()
    expect(await ui.find({ key: 'docket-s-ffff6666' })).toBeUndefined()
    // The concept counts its finished specs; a group without a concept and without an open spec is not drawn.
    expect(await ui.find({ type: 'Text', text: /^agreed · 1 open · 1 finished · 1 open decision$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Without a concept/ })).toBeUndefined()
    await ui.unmount()
  }

  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const again = async () => {
    await ui.unmount()
    ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  }

  // A head orders by its column, a second press turns the order round.
  await ui.press({ key: 'sort-title' })
  await again()
  expect((await ui.find({ key: 'sort-title' }))?.text).toBe('Title ▲')
  expect((await ui.find({ key: 'sort-closed' }))?.text).toBe('Closed')
  expect(await tableRows(ui, open)).toEqual(['spec-s-9999eeee', 'spec-s-ffff6666', 'spec-s-cccc3333'])
  await ui.press({ key: 'sort-title' })
  await again()
  expect((await ui.find({ key: 'sort-title' }))?.text).toBe('Title ▼')
  expect(await tableRows(ui, open)).toEqual(['spec-s-cccc3333', 'spec-s-ffff6666', 'spec-s-9999eeee'])
  // By concept: the specs without one first, by id; turned round, the concept's spec leads.
  await ui.press({ key: 'sort-concept' })
  await again()
  expect((await ui.find({ key: 'sort-concept' }))?.text).toBe('Concept ▲')
  expect((await ui.find({ key: 'sort-title' }))?.text).toBe('Title')
  expect(await tableRows(ui, open)).toEqual(['spec-s-9999eeee', 'spec-s-ffff6666', 'spec-s-cccc3333'])
  await ui.press({ key: 'sort-concept' })
  await again()
  expect((await ui.find({ key: 'sort-concept' }))?.text).toBe('Concept ▼')
  expect(await tableRows(ui, open)).toEqual(['spec-s-cccc3333', 'spec-s-9999eeee', 'spec-s-ffff6666'])
  await ui.press({ key: 'sort-closed' })
  await again()
  expect(await tableRows(ui, open)).toEqual(['spec-s-ffff6666', 'spec-s-9999eeee', 'spec-s-cccc3333'])
  await ui.press({ key: 'sort-closed' })
  await again()
  expect((await ui.find({ key: 'sort-closed' }))?.text).toBe('Closed ▲')
  expect(await tableRows(ui, open)).toEqual(['spec-s-cccc3333', 'spec-s-9999eeee', 'spec-s-ffff6666'])

  // Enter searches the titles of the finished specs, whatever the case; the open rows stay.
  await ui.input({ key: 'finished-search', text: ' LOGIN ' })
  await again()
  expect(await tableRows(ui, open)).toEqual(['spec-s-ffff6666'])
  expect(await ui.find({ key: 'spec-s-0123abcd' })).toBeDefined()
  // The text stands beside the count too: the terminal empties its field on Enter.
  expect(await ui.find({ type: 'Text', text: /^“LOGIN”: 1 of 3$/ })).toBeDefined()
  expect((await ui.find({ key: 'finished-search' }))?.props.value).toBe('LOGIN')

  // A pasted control character or half a surrogate pair never reaches the field's value: the engine refuses such a
  // tree, and the pane would close.
  await ui.input({ key: 'finished-search', text: 'log\u001b[0m\u0085in\ud83d' })
  await again()
  expect((await ui.find({ key: 'finished-search' }))?.props.value).toBe('log [0m in')

  // A search and an order belong to the project they were set in: another root starts with every finished spec,
  // the one closed last first.
  await ui.input({ key: 'finished-search', text: 'login' })
  root = '/other'
  seen.root = '/other'
  await $.classic.SessionStart({ source: 'startup' })
  await clock.settle()
  await again()
  expect((await ui.find({ key: 'finished-search' }))?.props.value).toBe('')
  expect((await ui.find({ key: 'sort-closed' }))?.text).toBe('Closed ▼')
  expect(await tableRows(ui, open)).toEqual(['spec-s-ffff6666', 'spec-s-9999eeee', 'spec-s-cccc3333'])

  await ui.input({ key: 'finished-search', text: 'invoice' })
  await again()
  expect(await tableRows(ui, open)).toEqual([])
  expect(await ui.find({ type: 'Text', text: /^No finished spec has this in its title\.$/ })).toBeDefined()

  await ui.press({ key: 'finished-clear' })
  await again()
  expect((await tableRows(ui, open)).length).toBe(3)
  expect(await ui.find({ key: 'finished-clear' })).toBeUndefined()

  // "Open only" folds the table into one line.
  await ui.press({ key: 'only-open' })
  await again()
  expect(await tableRows(ui, open)).toEqual([])
  expect(await ui.find({ key: 'finished-search' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^3 finished hidden$/ })).toBeDefined()
  await ui.unmount()
})

test('the table draws sixty finished specs and says so, and the search reaches the others', async ($, on) => {
  const specs = Array.from({ length: 70 }, (_, i) =>
    spec(`s-${String(i).padStart(8, '0')}`, `Step ${i}`, 'done', { docket: `/work/dockets/core/${i}.md` }),
  )
  const { clock } = world(on, () => overview({ concepts: [], specs }))

  await start($, clock)

  // Every surface takes the tree of sixty rows.
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })

    expect((await tableRows(ui)).length).toBe(60)
    expect(await ui.find({ type: 'Text', text: /^60 of 70 shown: the search reaches the others\.$/ })).toBeDefined()
    // A project without concepts has no column for them, and a spec no line of the LOG closed shows no day.
    expect(await ui.find({ key: 'sort-concept' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /^2026-11-01$/ })).toBeUndefined()
    await ui.unmount()
  }

  let ui = await $.ui.mount({ ...PANE, surface: 'desktop' })

  expect(await ui.find({ key: 'spec-s-00000069' })).toBeUndefined()
  await ui.input({ key: 'finished-search', text: 'step 69' })
  await ui.unmount()
  ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ key: 'spec-s-00000069' }))?.text).toBe('Step 69')
  expect(await ui.find({ type: 'Text', text: /shown/ })).toBeUndefined()
  await ui.unmount()
})

test('a project without docket gets a band that starts the setup and can be hidden for good', async ($, on) => {
  const { seen, clock } = world(on, () => ({ ok: true, initialized: false, root: '/project', work: '/w' }))

  // Once the band is hidden the plugin leaves the row to the engine: this hook stands for its drawing.
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
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

test('the desktop app gets the name and the version as one label of the footer, the terminal keeps the status line', async ($, on) => {
  const { seen, clock } = world(on, overview)
  const line = '1.2.3'
  const label = `docket ${line}`

  await start($, clock)
  expect(seen.status.at(-1)).toBe(line)

  // The terminal draws one size everywhere: its footer stays the engine's, and the status line stays.
  let ui = await $.ui.mount({ ...MODES, surface: 'terminal' })

  expect((await ui.find({ type: 'Text' }))?.text).toBe('engine: ')
  await ui.unmount()
  expect(seen.status.at(-1)).toBe(line)

  // The desktop app draws a status line's name smaller than its text. Once it asks for the footer's label, the
  // plugin draws the name and the version there as one text, and the status line is taken back.
  ui = await $.ui.mount({ ...MODES, surface: 'desktop' })
  expect((await ui.find({ type: 'Text' }))?.text).toBe(label)
  await ui.unmount()
  expect(seen.status.at(-1)).toBeUndefined()

  // A mode of the engine's own stays before it.
  ui = await $.ui.mount({ ...MODES, props: { modes: ['focus'] }, surface: 'desktop' })
  expect((await ui.find({ type: 'Text' }))?.text).toBe(`focus & ${label}`)
  await ui.unmount()

  // A later read does not set the status line again.
  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })

  await pane.press({ key: 'refresh' })
  await clock.settle()
  await pane.unmount()
  expect(seen.status.at(-1)).toBeUndefined()
})
