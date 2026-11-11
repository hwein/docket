// The docket pane: the register, the runs, the LOG and DOCKET.md beside the chat, with buttons for what the
// commands do. It shows what hooks/docket_ui.py reads and changes nothing but through it and the docket commands.

import type { EngineInterface, Register } from 'claude-code'

import { call, readBody } from './data'
import type { Overview, Run, Spec } from './data'
import { words } from './strings'
import { canRun, drawBand, drawPane } from './view'
import type { Actions, PaneState } from './view'

const PANE = 'docket'
const START_SESSION = 'mcp__ccd_session__start_session'
const HIDDEN_KEY = 'band-hidden'
const SOON = 2000
// A sealed run calls tools all the time; the register is read at most this often for them.
const TOOL_PACE = 10000
// A run of another session changes files this session hears nothing of.
const FOREIGN_PACE = 15000

const S: PaneState = {
  data: undefined,
  error: '',
  loadedAt: '',
  tab: 'register',
  openSpec: '',
  detail: 'spec',
  isSuperseding: false,
  supersedeBy: '',
  isOnlyOpen: false,
  notice: '',
  logo: '',
  hasVectors: false,
  picked: [],
}

const bodies = new Map<string, string | undefined>()
let isBandHidden = false
let isReading = false
let isAskedAgain = false
let timer: { cancel: () => void } | undefined
let lastRead = 0
// The session root the last read was made for; undefined before the first.
let readRoot: string | undefined

const t = () => words(S.data?.language)

const problem = (code: string): string => t().errors[code] ?? t().errors.failed ?? code

const redraw = ($: EngineInterface): void => {
  $.ui.invalidate('ui.render')
}

const statusLine = (data: Overview): string | undefined => {
  const counts = data.counts

  if (!data.initialized || counts === undefined) {
    return undefined
  }

  const say = t()
  const line = say.status(
    counts.open ?? 0,
    counts.draft ?? 0,
    counts.ready ?? 0,
    counts['in-progress'] ?? 0,
    counts.done ?? 0,
    counts.superseded ?? 0,
  )
  const mine = data.runs?.find(run => run.mine)

  if (mine === undefined) {
    return line
  }

  const points = mine.points
    ? `${mine.points.done}/${mine.points.done + mine.points.deviating + mine.points.open}`
    : ''

  return `${say.statusRun(mine.title, points).trim()} · ${line}`
}

/** A run that was there and is gone has ended: say how, once, where the person sees it without the pane. */
const announce = ($: EngineInterface, before: Overview | undefined, after: Overview): void => {
  const say = t()

  for (const run of before?.runs ?? []) {
    const now = after.runs?.find(other => other.session === run.session)

    if (now !== undefined && now.spec === run.spec) {
      continue
    }

    const spec = after.specs?.find(found => found.id === run.spec)
    const kind = spec?.result?.kind
    const text = kind ? say.ended(run.title, say.results[kind] ?? kind) : say.endedPlain(run.title)

    $.ui.toast(text)

    if (now === undefined) {
      void $.ui.notify(text, { title: 'docket', key: `run-${run.session}` }).catch(() => undefined)
    }
  }
}

const schedule = ($: EngineInterface, ms: number): void => {
  if (timer !== undefined) {
    return
  }

  timer = $.clock.after(ms, () => {
    timer = undefined
    void refresh($)
  })
}

const refresh = async ($: EngineInterface): Promise<void> => {
  if (isReading) {
    isAskedAgain = true

    return
  }

  isReading = true

  try {
    readRoot = await $.session.root()

    const reply = await call<Overview>($, 'overview')
    const before = S.data
    const now = await $.clock.now()

    lastRead = now
    S.loadedAt = new Date(now).toTimeString().slice(0, 5)

    if (reply.ok) {
      S.data = reply
      S.error = ''
      // A tick stays only while its spec can still run.
      S.picked = S.picked.filter(id => reply.specs?.some(spec => spec.id === id && canRun(spec)))
      bodies.clear()
      announce($, before, reply)

      // The first read of a docket project opens the pane, and so does the read after the session moved: the app
      // starts a session's process in a placeholder folder before the first prompt and moves it to the project
      // with that prompt (luradb-web-client 2026-11-10: the pane came up only after the first turn had ended).
      if (reply.initialized && (before === undefined || !before.initialized || before.root !== reply.root)) {
        void $.ui.open({ id: PANE, title: t().title }).catch(() => undefined)
      }

      if (reply.runs?.some(run => !run.mine)) {
        schedule($, FOREIGN_PACE)
      }
    } else {
      const say = t()

      S.error = `${problem(reply.error)}${reply.detail ? ` (${reply.detail})` : ''}`
    }

    redraw($)
  } catch {
    S.error = problem('failed')
    redraw($)
  } finally {
    isReading = false

    if (isAskedAgain) {
      isAskedAgain = false
      void refresh($)
    }
  }
}

/** Reads again at once where the session stands in another folder than the last read's, or nothing was read yet. */
const follow = async ($: EngineInterface): Promise<void> => {
  const root = await $.session.root().catch(() => readRoot)

  if (root !== undefined && root !== readRoot) {
    // The band's "hide for good" is kept per project.
    isBandHidden = (await hiddenRoots($)).includes(root)
    await refresh($)
  } else if (S.data === undefined && !isReading) {
    await refresh($)
  }
}

/** Runs a docket command as the person's own: the hooks seal, stop or set up on the command line they read. */
const runCommand = async ($: EngineInterface, command: string, args: string): Promise<boolean> => {
  const isListed = await $.command
    .list()
    .then(found => found.some(listed => listed.name === command))
    .catch(() => false)

  if (isListed) {
    try {
      await $.command.run({ command, args })

      return true
    } catch {
      // The hooks or the engine refused it and said why in the transcript: sending it again would say it twice.
      return false
    }
  }

  // Not listed under that name in this session: the command on a line of its own below a preamble, where the
  // hooks read it as well (the engine refuses a plugin's prompt that begins with a slash).
  try {
    const sent = await $.prompt.submit({ text: `${t().fromPane}\n${`/${command} ${args}`.trim()}`, asUser: true })

    return !('drop' in sent && sent.drop !== undefined)
  } catch {
    return false
  }
}

const implement = async ($: EngineInterface, target: string, label: string): Promise<void> => {
  S.notice = t().started(label)
  S.picked = []
  redraw($)

  if (!(await runCommand($, 'docket:implement', target))) {
    S.notice = t().notStarted
    redraw($)
  }

  schedule($, SOON)
}

/**
 * Puts a request into the empty prompt box for the person to send: the app starts a session only on a turn the
 * person began, and refuses it on a prompt this plugin submits (luradb-web-client 2026-11-10: the pane's request
 * was refused, the same call succeeded on the next typed turn). False where no box takes it or a draft is in it.
 */
const fillRequest = async ($: EngineInterface, text: string): Promise<boolean> => {
  try {
    if ((await $.prompt.read()).text.trim() !== '') {
      return false
    }

    return (await $.prompt.fill({ text })).isFilled
  } catch {
    return false
  }
}

/**
 * The same run in a session of its own: started through the app's session tool, else requested in the prompt box
 * for the person's Enter, else by asking Claude for it.
 */
const implementInSession = async ($: EngineInterface, target: string, label: string): Promise<void> => {
  const say = t()
  const command = `/docket:implement ${target}`
  let hasStarted = false

  S.picked = []
  // The app wraps the brief of the session it starts, so the command is neither its first nor its last line: the
  // hooks of that session take it from this record (docket_lib.take_handoff).
  await call<{ ok: true }>($, 'handoff', { command }).catch(() => undefined)

  try {
    const ran = await $.tool.call({
      tool: START_SESSION,
      initiation: 'user_asked',
      title: `docket: ${label}`.slice(0, 58),
      prompt: `${say.briefLead}\n\n${command}`,
      background: say.briefBackground,
      context: 'fresh',
      use_worktree: false,
    })

    hasStarted = ran.deny === undefined && ran.isError !== true
  } catch {
    // no such tool in this session, or the call was not allowed
  }

  if (hasStarted) {
    S.notice = say.sessionStarted(label)
  } else if (await fillRequest($, say.askSession(command))) {
    S.notice = say.sessionFilled(label)
  } else {
    try {
      await $.prompt.submit({ text: say.askSession(command), asUser: true })
      S.notice = say.sessionAsked(label)
    } catch {
      S.notice = say.notStarted
    }
  }

  redraw($)
  schedule($, FOREIGN_PACE)
}

const supersede = async ($: EngineInterface, spec: Spec, by: string): Promise<void> => {
  const say = t()
  const reply = await call<{ ok: true }>($, 'supersede', { spec: spec.id, by })

  S.isSuperseding = false
  S.supersedeBy = ''
  S.notice = reply.ok ? say.superseded(spec.title) : problem(reply.error)
  await refresh($)
}

const stop = async ($: EngineInterface, run: Run): Promise<void> => {
  const say = t()

  if (run.mine) {
    S.notice = (await runCommand($, 'docket:stop', '')) ? say.stopSent : say.notStarted
  } else {
    const reply = await call<{ ok: true }>($, 'stop', { session: run.session })

    S.notice = reply.ok ? say.stopSent : problem(reply.error)
  }

  await refresh($)
}

const actions = ($: EngineInterface): Actions => {
  const show = (change: () => void) => () => {
    change()
    redraw($)
  }

  return {
    refresh: () => void refresh($),
    setTab: tab =>
      show(() => {
        S.tab = tab
        S.openSpec = ''
        S.notice = ''
      })(),
    openSpec: id =>
      show(() => {
        S.openSpec = id
        S.detail = 'spec'
        S.isSuperseding = false
        S.supersedeBy = ''
        S.notice = ''
      })(),
    openDocket: id =>
      show(() => {
        S.openSpec = id
        S.detail = 'docket'
        S.isSuperseding = false
        S.supersedeBy = ''
        S.notice = ''
      })(),
    back: show(() => {
      S.openSpec = ''
      S.isSuperseding = false
      S.notice = ''
    }),
    setDetail: detail =>
      show(() => {
        S.detail = detail
      })(),
    toggleOnlyOpen: show(() => {
      S.isOnlyOpen = !S.isOnlyOpen
    }),
    togglePick: id =>
      show(() => {
        S.picked = S.picked.includes(id) ? S.picked.filter(other => other !== id) : [...S.picked, id]
      })(),
    clearPicks: show(() => {
      S.picked = []
    }),
    implement: (target, label) => void implement($, target, label),
    implementInSession: (target, label) => void implementInSession($, target, label),
    supersedeOpen: show(() => {
      S.isSuperseding = true
    }),
    supersedePick: id =>
      show(() => {
        S.supersedeBy = id
      })(),
    supersedeCancel: show(() => {
      S.isSuperseding = false
      S.supersedeBy = ''
    }),
    supersedeConfirm: (spec, by) => void supersede($, spec, by),
    stop: run => void stop($, run),
  }
}

/** The file the view in front shows: DOCKET.md, a spec or its docket; read once per refresh. */
const content = async ($: EngineInterface): Promise<string | undefined> => {
  const data = S.data
  const spec = S.openSpec ? data?.specs?.find(found => found.id === S.openSpec) : undefined
  const path =
    spec === undefined
      ? S.tab === 'config'
        ? data?.config
        : undefined
      : S.detail === 'spec'
        ? spec.path
        : S.detail === 'docket'
          ? spec.docket
          : undefined

  if (!path) {
    return undefined
  }

  if (!bodies.has(path)) {
    bodies.set(path, await readBody($, path))
  }

  return bodies.get(path)
}

const hiddenRoots = async ($: EngineInterface): Promise<string[]> => {
  const stored = await $.store.get(HIDDEN_KEY).catch(() => undefined)

  return Array.isArray(stored) ? stored.filter((root): root is string => typeof root === 'string') : []
}

// The foreground of the logo follows the surface's light or dark scheme where it honours the style; the
// attribute beside it is a grey that reads on both where it does not.
const LOGO_STYLE = '<style>.f{fill:#1F2328}@media (prefers-color-scheme:dark){.f{fill:#E6EDF3}}</style>'

const readLogo = async ($: EngineInterface): Promise<string> => {
  try {
    const svg = await $.fs.read(`${$.plugin.root}/assets/logo-dark.svg`)

    return svg.replaceAll('fill="#E6EDF3"', 'class="f" fill="#8B949E"').replace('<g ', `${LOGO_STYLE}<g `)
  } catch {
    return ''
  }
}

const boot = async ($: EngineInterface): Promise<void> => {
  S.logo = await readLogo($)
  isBandHidden = (await hiddenRoots($)).includes(await $.session.root())
  await refresh($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'docket-pane',
      description: 'Show the docket pane: register, runs, LOG and DOCKET.md',
    })
    void boot($).catch(() => undefined)

    return next(e)
  })

  on('command.run', { command: 'docket-pane' }, async $ => {
    await refresh($)
    const opened = await $.ui.open({ id: PANE, title: t().title })

    return { text: opened.isPlaced ? 'docket pane opened.' : `docket pane not shown: ${opened.reason}` }
  })

  // The session start hooks run when the app moves the session from its placeholder folder to the project, with
  // the first prompt: the project is read then, not after the first turn.
  on('classic.SessionStart', ($, e, next) => {
    void follow($).catch(() => undefined)

    return next(e)
  })

  on('session.attach', ($, e, next) => {
    void follow($).catch(() => undefined)

    return next(e)
  })

  // The hooks seal a session and move a spec's state on a prompt; a turn's end is when a run has moved on.
  on('turn.start', ($, e, next) => {
    if (S.data?.initialized) {
      schedule($, SOON)
    }

    void follow($).catch(() => undefined)

    return next(e)
  })

  // Also where docket is not set up yet: a turn that ran /docket:init ends with a DOCKET.md, and the pane opens.
  on('turn.complete', ($, e, next) => {
    if (S.data?.initialized || (S.data !== undefined && !isBandHidden)) {
      schedule($, SOON)
    }

    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    const ran = await next(e)

    if (S.data?.initialized && timer === undefined) {
      const now = await $.clock.now()

      schedule($, Math.max(SOON, TOOL_PACE - (now - lastRead)))
    }

    return ran
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    if (S.data === undefined && S.error === '' && !isReading) {
      void refresh($)
    }

    S.hasVectors = e.surface !== 'terminal'

    return drawPane($.ui.resolve(e), S, t(), actions($), await content($))
  })

  // The counts stand among the footer's mode labels, the name and the figures as one text the surface draws in one
  // size: as a status line (`$.ui.status`) the desktop app drew the plugin's name as a label of its own, smaller
  // than the text beside it (2026-11-11).
  on('ui.render', { component: 'SessionMode' }, ($, e, next) => {
    const line = S.data ? statusLine(S.data) : undefined

    if (line === undefined) {
      return next(e)
    }

    return next({ ...e, props: { ...e.props, modes: [...e.props.modes, `docket: ${line}`] } })
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (S.data === undefined || S.data.initialized || isBandHidden || e.props.hasSurvey) {
      return next(e)
    }

    return drawBand($.ui.resolve(e), t(), {
      init: () => {
        void runCommand($, 'docket:init', '').then(() => schedule($, SOON))
      },
      hide: () => {
        isBandHidden = true
        redraw($)
        void (async () => {
          const root = await $.session.root()
          const roots = await hiddenRoots($)

          if (!roots.includes(root)) {
            await $.store.set(HIDDEN_KEY, [...roots, root])
          }
        })().catch(() => undefined)
      },
    })
  })
}
