// What the pane and the band draw. Nothing here reads a file or calls the engine: the hooks module hands in the
// state, the words and the actions.

import type { Color, Elements } from 'claude-code'

import type { Concept, Overview, Run, Spec } from './data'
import type { Words } from './strings'

// `Svg` is the graphical surfaces' alone: the terminal draws the name as text.
export type Kit = Pick<Elements['desktop'], 'Box' | 'Text' | 'Button' | 'Select' | 'Markdown'> &
  Partial<Pick<Elements['desktop'], 'Svg'>>

export type Tab = 'register' | 'log' | 'config'
export type Detail = 'spec' | 'docket' | 'log'

export type PaneState = {
  data: Overview | undefined
  error: string
  loadedAt: string
  tab: Tab
  openSpec: string
  detail: Detail
  isSuperseding: boolean
  supersedeBy: string
  isOnlyOpen: boolean
  notice: string
  /** The logo's SVG document, '' until it is read or where it cannot be. */
  logo: string
  /** Whether the surface drawing now draws vectors: every one but the terminal. */
  hasVectors: boolean
  /** Ready specs ticked in the register, to run together as one series. */
  picked: string[]
}

export type Actions = {
  refresh: () => void
  setTab: (tab: Tab) => void
  openSpec: (id: string) => void
  openDocket: (id: string) => void
  back: () => void
  setDetail: (detail: Detail) => void
  toggleOnlyOpen: () => void
  togglePick: (id: string) => void
  clearPicks: () => void
  implement: (target: string, label: string) => void
  implementInSession: (target: string, label: string) => void
  supersedeOpen: () => void
  supersedePick: (id: string) => void
  supersedeCancel: () => void
  supersedeConfirm: (spec: Spec, by: string) => void
  stop: (run: Run) => void
}

export type BandActions = { init: () => void; hide: () => void }

export const STATES = ['draft', 'ready', 'in-progress', 'done', 'superseded'] as const
const ORDER = ['in-progress', 'ready', 'draft', 'done', 'superseded']
const FINISHED = ['done', 'superseded']
const GLYPH: Record<string, string> = {
  draft: '○',
  ready: '●',
  'in-progress': '◐',
  done: '✔',
  superseded: '⊘',
}
const COLOR: Record<string, Color> = {
  draft: 'warning',
  ready: 'suggestion',
  'in-progress': 'claude',
  done: 'success',
  superseded: 'inactive',
}
const LOG_ROWS = 100
const LEAD_HEADING = /^\s*#\s[^\n]*\n+/

const stateText = (t: Words, state: string): string => `${GLYPH[state] ?? '·'} ${t.states[state] ?? state}`

const byState = (specs: readonly Spec[]): Spec[] =>
  [...specs].sort((a, b) => {
    const order = ORDER.indexOf(a.state) - ORDER.indexOf(b.state)

    return order !== 0 ? order : a.created.localeCompare(b.created) || a.id.localeCompare(b.id)
  })

const titleOf = (data: Overview, id: string): string => data.specs?.find(spec => spec.id === id)?.title ?? id

const progress = (t: Words, run: Run): string => {
  const points = run.points
    ? t.runPoints(run.points.done, run.points.done + run.points.deviating + run.points.open)
    : t.preparing

  return run.n > 1 ? `${t.runOf(run.i, run.n)} · ${points}` : points
}

/** What holds a spec or stands out about it, as one dim line part. */
const marks = (t: Words, data: Overview, spec: Spec): string => {
  const found: string[] = []

  if (spec.running) {
    found.push('▶')
  }

  if (spec.waits_for.length > 0) {
    found.push(`${t.waitsFor}: ${spec.waits_for.map(id => titleOf(data, id)).join(', ')}`)
  }

  if (spec.decisions > 0) {
    found.push(t.decisions(spec.decisions))
  }

  if (spec.result && spec.result.kind !== 'delivered') {
    found.push(t.results[spec.result.kind] ?? spec.result.kind)
  }

  if (spec.state === 'done' && spec.points && spec.points.deviating > 0) {
    found.push(`~${spec.points.deviating}`)
  }

  return found.join(' · ')
}

/** A spec that can go into a run now: ready, and not running anywhere. */
export const canRun = (spec: Spec): boolean => spec.state === 'ready' && !spec.running

/** The ticked specs that can still run, in implementation order: the order a series of them runs in. */
export const pickedSpecs = (data: Overview, picked: readonly string[]): Spec[] =>
  (data.specs ?? []).filter(spec => picked.includes(spec.id) && canRun(spec)).sort((a, b) => a.order - b.order)

/** The first ticked spec whose predecessor is neither done nor ticked: the hooks would refuse that series. */
const pickGap = (data: Overview, specs: readonly Spec[]): { spec: Spec; before: string } | undefined => {
  const ids = new Set(specs.map(spec => spec.id))

  for (const spec of specs) {
    const before = spec.waits_for.find(id => !ids.has(id))

    if (before !== undefined) {
      return { spec, before: titleOf(data, before) }
    }
  }

  return undefined
}

const specRow = (kit: Kit, t: Words, data: Overview, spec: Spec, s: PaneState, act: Actions) => {
  const { Box, Button, Text } = kit
  const note = marks(t, data, spec)
  const parts = [<Text color={COLOR[spec.state] ?? 'text'}>{stateText(t, spec.state)}</Text>, `  ${spec.title}`]

  if (note) {
    parts.push(<Text dimColor>{`  ${note}`}</Text>)
  }

  return (
    <Box flexDirection="row" gap="sm" alignItems="center">
      {canRun(spec) ? (
        <Button
          key={`pick-${spec.id}`}
          plain
          label={s.picked.includes(spec.id) ? '☑' : '☐'}
          onPress={() => act.togglePick(spec.id)}
        />
      ) : (
        <Text dimColor>{'  '}</Text>
      )}
      <Button
        key={`spec-${spec.id}`}
        plain
        dimColor={FINISHED.includes(spec.state)}
        onPress={() => act.openSpec(spec.id)}
      >
        {parts}
      </Button>
      {spec.state === 'done' && spec.docket !== '' && (
        <Button key={`docket-${spec.id}`} plain dimColor label={`[${t.docket}]`} onPress={() => act.openDocket(spec.id)} />
      )}
    </Box>
  )
}

/** What is ticked, and the buttons that run it as one series: here or in a session of its own. */
const pickBar = (kit: Kit, t: Words, data: Overview, s: PaneState, act: Actions) => {
  const { Box, Button, Text } = kit
  const specs = pickedSpecs(data, s.picked)

  if (specs.length === 0) {
    return (data.specs ?? []).some(canRun) ? (
      <Text dimColor size="caption">
        {t.pickHint}
      </Text>
    ) : (
      false
    )
  }

  const gap = pickGap(data, specs)
  const target = specs.map(spec => spec.id).join(', ')
  const titles = specs.map(spec => spec.title).join(', ')

  return (
    <Box flexDirection="column" gap="xs" borderStyle="round" borderColor="suggestion" paddingX="sm">
      <Text>{t.picked(specs.length, titles)}</Text>
      {gap !== undefined && <Text color="warning">{t.picksBlocked(gap.spec.title, gap.before)}</Text>}
      <Box flexDirection="row" gap="sm" flexWrap="wrap">
        {gap === undefined && (
          <Button
            key="picked-implement"
            variant="primary"
            label={t.implementPicked(specs.length)}
            onPress={() => act.implement(target, titles)}
          />
        )}
        {gap === undefined && (
          <Button
            key="picked-session"
            label={t.pickedInSession(specs.length)}
            onPress={() => act.implementInSession(target, titles)}
          />
        )}
        <Button key="picked-clear" label={t.clearPicks} onPress={act.clearPicks} />
      </Box>
    </Box>
  )
}

const group = (
  kit: Kit,
  t: Words,
  data: Overview,
  concept: Concept | undefined,
  specs: readonly Spec[],
  hasHeading: boolean,
  s: PaneState,
  act: Actions,
) => {
  const { Box, Button, Text } = kit
  const shown = byState(s.isOnlyOpen ? specs.filter(spec => !FINISHED.includes(spec.state)) : specs)
  const hidden = specs.length - shown.length
  const ready = specs.filter(spec => spec.state === 'ready').length
  const open = specs.filter(spec => !FINISHED.includes(spec.state)).length
  const meta = concept
    ? [
        t.conceptStates[concept.state] ?? concept.state,
        `${open} ${t.open.toLowerCase()}`,
        concept.decisions > 0 ? t.decisions(concept.decisions) : '',
      ]
        .filter(Boolean)
        .join(' · ')
    : `${open} ${t.open.toLowerCase()}`

  return (
    <Box flexDirection="column" gap="xs" marginTop="sm">
      {hasHeading && (
        <Box flexDirection="row" gap="sm" flexWrap="wrap" alignItems="center">
          <Text weight="semibold" size="heading">
            {concept ? concept.title || concept.id : t.noConcept}
          </Text>
          <Text dimColor>{meta}</Text>
        </Box>
      )}
      {concept !== undefined && ready > 0 && (
        <Box flexDirection="row" gap="sm" flexWrap="wrap">
          <Button
            key={`all-${concept.id}`}
            variant="primary"
            label={t.implementAll(ready)}
            onPress={() => act.implement(concept.id, concept.title || concept.id)}
          />
          <Button
            key={`all-session-${concept.id}`}
            label={t.allInSession(ready)}
            onPress={() => act.implementInSession(concept.id, concept.title || concept.id)}
          />
        </Box>
      )}
      {specs.length === 0 && <Text dimColor>{t.noSpecsInConcept}</Text>}
      {shown.map(spec => specRow(kit, t, data, spec, s, act))}
      {hidden > 0 && <Text dimColor>{t.hiddenDone(hidden)}</Text>}
    </Box>
  )
}

const registerView = (kit: Kit, t: Words, data: Overview, s: PaneState, act: Actions) => {
  const { Box, Button, Text } = kit
  const specs = data.specs ?? []
  const concepts = data.concepts ?? []
  const known = new Set(concepts.map(concept => concept.id))
  const loose = specs.filter(spec => !known.has(spec.concept))
  const hasFinished = specs.some(spec => FINISHED.includes(spec.state))

  return (
    <Box flexDirection="column">
      {hasFinished && (
        <Box flexDirection="row">
          <Button key="only-open" label={s.isOnlyOpen ? t.showAll : t.onlyOpen} onPress={act.toggleOnlyOpen} />
        </Box>
      )}
      {pickBar(kit, t, data, s, act)}
      {specs.length === 0 && concepts.length === 0 && <Text dimColor>{t.noSpecs}</Text>}
      {concepts.map(concept =>
        group(
          kit,
          t,
          data,
          concept,
          specs.filter(spec => spec.concept === concept.id),
          true,
          s,
          act,
        ),
      )}
      {loose.length > 0 && group(kit, t, data, undefined, loose, concepts.length > 0, s, act)}
    </Box>
  )
}

const cell = (text: string): string => text.replace(/\|/g, '\\|').replace(/[\r\n]+/g, ' ')

/** LOG lines as a markdown table, newest first: `<time>  <doc>  <event>  session=<id>  <note>`. */
export const logTable = (t: Words, data: Overview, lines: readonly string[]): string => {
  const rows = [...lines]
    .reverse()
    .slice(0, LOG_ROWS)
    .map(line => {
      const [time = '', doc = '', event = '', ...rest] = line.trim().split(/\s+/)
      const note = rest.filter(part => !part.startsWith('session=')).join(' ')
      const when = time.replace(/^\d{4}-(\d\d-\d\d)T(\d\d:\d\d).*$/, '$1 $2')

      return `| ${cell(when)} | ${cell(doc === '-' ? '' : titleOf(data, doc))} | ${cell(event)} | ${cell(note)} |`
    })

  return rows.length === 0
    ? t.noLog
    : [`| ${t.logHead.join(' | ')} |`, '|---|---|---|---|', ...rows].join('\n')
}

const fact = (kit: Kit, label: string, value: string, color?: Color) => {
  const { Box, Text } = kit

  return (
    <Box flexDirection="row" gap="sm" flexWrap="wrap">
      <Text dimColor>{label}</Text>
      <Text color={color ?? 'text'}>{value}</Text>
    </Box>
  )
}

const supersedeForm = (kit: Kit, t: Words, data: Overview, spec: Spec, s: PaneState, act: Actions) => {
  const { Box, Button, Select, Text } = kit
  // A spec that names this one in `follows:` reworks it: those come first.
  const others = (data.specs ?? []).filter(other => other.id !== spec.id && other.state !== 'superseded')
  const options = [
    ...others.filter(other => other.follows.includes(spec.id)),
    ...others.filter(other => !other.follows.includes(spec.id)),
  ].map(other => ({ value: other.id, label: `${other.title} (${t.states[other.state] ?? other.state})` }))
  const first = options[0]

  if (first === undefined) {
    return (
      <Box flexDirection="row" gap="sm" flexWrap="wrap" alignItems="center">
        <Text dimColor>{t.noSuccessor}</Text>
        <Button key="supersede-cancel" label={t.cancel} onPress={act.supersedeCancel} />
      </Box>
    )
  }

  const by = options.some(option => option.value === s.supersedeBy) ? s.supersedeBy : first.value

  return (
    <Box flexDirection="column" gap="xs" borderStyle="round" borderColor="warning" paddingX="sm">
      <Select key="supersede-by" label={t.replacedBy} options={options} value={by} onSelect={act.supersedePick} />
      <Box flexDirection="row" gap="sm" flexWrap="wrap">
        <Button
          key="supersede-confirm"
          variant="primary"
          label={t.confirm}
          onPress={() => act.supersedeConfirm(spec, by)}
        />
        <Button key="supersede-cancel" label={t.cancel} onPress={act.supersedeCancel} />
      </Box>
    </Box>
  )
}

const specView = (
  kit: Kit,
  t: Words,
  data: Overview,
  spec: Spec,
  s: PaneState,
  act: Actions,
  content: string | undefined,
) => {
  const { Box, Button, Markdown, Text } = kit
  const concept = (data.concepts ?? []).find(found => found.id === spec.concept)
  const isRunnable = canRun(spec)
  const canSupersede = spec.state !== 'superseded' && spec.state !== 'in-progress'
  const tab = (detail: Detail, label: string) => (
    <Button
      key={`detail-${detail}`}
      variant={s.detail === detail ? 'primary' : 'secondary'}
      label={label}
      onPress={() => act.setDetail(detail)}
    />
  )
  const body =
    s.detail === 'log'
      ? logTable(
          t,
          data,
          (data.log ?? []).filter(line => line.split(/\s+/)[1] === spec.id),
        )
      : s.detail === 'docket' && !spec.docket
        ? t.noDocket
        : (content ?? t.unreadable)

  // The card above already carries the title: the file's own first heading would say it twice.
  const shown = s.detail === 'log' ? body : body.replace(LEAD_HEADING, '')

  return (
    <Box flexDirection="column" gap="sm">
      <Box flexDirection="row">
        <Button key="back" plain dimColor label={t.back} onPress={act.back} />
      </Box>
      <Box
        flexDirection="column"
        gap="xs"
        borderStyle="round"
        borderColor="inactive"
        paddingX="md"
        paddingY="sm"
      >
      <Text size="title" weight="semibold" strikethrough={spec.state === 'superseded'}>
        {spec.title}
      </Text>
      <Box flexDirection="row" gap="md" flexWrap="wrap">
        <Text color={COLOR[spec.state] ?? 'text'} weight="semibold">
          {stateText(t, spec.state)}
        </Text>
        <Text dimColor>{spec.id}</Text>
        {spec.created !== '' && <Text dimColor>{`${t.created} ${spec.created}`}</Text>}
      </Box>
      {concept !== undefined && fact(kit, t.concept, concept.title || concept.id)}
      {spec.follows.length > 0 &&
        fact(kit, t.follows, spec.follows.map(id => titleOf(data, id)).join(', '))}
      {spec.waits_for.length > 0 &&
        fact(kit, t.waitsFor, spec.waits_for.map(id => titleOf(data, id)).join(', '), 'warning')}
      {spec.decisions > 0 && fact(kit, t.decisionsLabel, String(spec.decisions), 'warning')}
      {spec.points !== null &&
        fact(kit, t.pointsLabel, t.points(spec.points.done, spec.points.deviating, spec.points.open))}
      {spec.result !== null &&
        fact(
          kit,
          t.result,
          `${t.results[spec.result.kind] ?? spec.result.kind}${spec.result.text ? ` – ${spec.result.text}` : ''}`,
          spec.result.kind === 'delivered' ? 'success' : 'warning',
        )}
      {spec.superseded_by !== '' && fact(kit, t.supersededBy, titleOf(data, spec.superseded_by))}
      {spec.running !== '' &&
        fact(
          kit,
          '▶',
          data.runs?.some(run => run.mine && run.spec === spec.id)
            ? t.runningHere
            : t.runningIn(spec.running.slice(0, 8)),
          'claude',
        )}
      {isRunnable && !s.isSuperseding && (
        <Box flexDirection="row" gap="sm" flexWrap="wrap" marginTop="xs">
          <Button
            key="implement"
            variant="primary"
            label={t.implement}
            onPress={() => act.implement(spec.id, spec.title)}
          />
          <Button
            key="implement-session"
            label={t.inSession}
            onPress={() => act.implementInSession(spec.id, spec.title)}
          />
        </Box>
      )}
      </Box>
      {s.isSuperseding && canSupersede && supersedeForm(kit, t, data, spec, s, act)}
      <Box flexDirection="row" gap="sm" flexWrap="wrap" alignItems="center" justifyContent="space-between">
        <Box flexDirection="row" gap="xs">
          {tab('spec', t.spec)}
          {tab('docket', t.docket)}
          {tab('log', t.log)}
        </Box>
        {canSupersede && !s.isSuperseding && (
          <Button key="supersede" plain dimColor label={t.markSuperseded} onPress={act.supersedeOpen} />
        )}
      </Box>
      <Markdown text={shown.trim() === '' ? t.unreadable : shown} />
    </Box>
  )
}

const runBanner = (kit: Kit, t: Words, run: Run, act: Actions) => {
  const { Box, Button, Text } = kit

  return (
    <Box
      flexDirection="row"
      gap="sm"
      flexWrap="wrap"
      alignItems="center"
      borderStyle="round"
      borderColor="claude"
      paddingX="sm"
    >
      <Text color="claude" weight="semibold">{`▶ ${t.run}`}</Text>
      <Text weight="semibold">{run.title}</Text>
      <Text dimColor>
        {run.mine ? progress(t, run) : `${progress(t, run)} · ${t.otherSession(run.session.slice(0, 8))}`}
      </Text>
      {run.stop_requested ? (
        <Text color="warning">{t.stopRequested}</Text>
      ) : (
        <Button key={`stop-${run.session}`} label={run.mine ? t.stop : t.stopAfter} onPress={() => act.stop(run)} />
      )}
    </Box>
  )
}

const LOGO_HEIGHT = 30
// The logo file is 203.1 by 52.
const LOGO_WIDTH = Math.round((LOGO_HEIGHT * 203.1) / 52)

/** The pane's head: the logo where the surface draws vectors, the name in title size elsewhere. */
const head = (kit: Kit, s: PaneState, t: Words) => {
  const { Box, Svg, Text } = kit

  return (
    <Box flexDirection="row" alignItems="center" marginRight="sm">
      {Svg !== undefined && s.hasVectors && s.logo !== '' ? (
        <Svg source={s.logo} alt={t.title} width={LOGO_WIDTH} height={LOGO_HEIGHT} />
      ) : (
        <Text size="title" weight="bold">
          {t.title}
        </Text>
      )}
    </Box>
  )
}

/** The pane: tabs and counters on top, then the register, one spec, the LOG or DOCKET.md. */
export const drawPane = (kit: Kit, s: PaneState, t: Words, act: Actions, content: string | undefined) => {
  const { Box, Button, Markdown, Text } = kit
  const data = s.data

  if (data === undefined || !data.initialized) {
    return (
      <Box flexDirection="column" gap="xs">
        {head(kit, s, t)}
        <Text color={s.error ? 'error' : 'text'} dimColor={!s.error}>
          {s.error || (data === undefined ? t.loading : t.bandText)}
        </Text>
        <Box flexDirection="row">
          <Button key="refresh" label={`↻ ${t.refresh}`} onPress={act.refresh} />
        </Box>
      </Box>
    )
  }

  const counts = data.counts ?? {}
  const spec = s.openSpec ? data.specs?.find(found => found.id === s.openSpec) : undefined
  const tab = (name: Tab, label: string) => (
    <Button
      key={`tab-${name}`}
      variant={s.tab === name && spec === undefined ? 'primary' : 'secondary'}
      label={label}
      onPress={() => act.setTab(name)}
    />
  )

  return (
    <Box flexDirection="column" gap="xs">
      <Box flexDirection="row" gap="sm" flexWrap="wrap" alignItems="center">
        {head(kit, s, t)}
        {tab('register', t.register)}
        {tab('log', t.log)}
        {tab('config', t.config)}
        <Button key="refresh" label={`↻ ${t.refresh}`} onPress={act.refresh} />
      </Box>
      {spec === undefined && (
        <Box flexDirection="row" gap="md" flexWrap="wrap">
          <Text weight="semibold">{`${t.open} ${counts.open ?? 0}`}</Text>
          {STATES.map(state => (
            <Text color={COLOR[state] ?? 'text'}>{`${stateText(t, state)} ${counts[state] ?? 0}`}</Text>
          ))}
        </Box>
      )}
      {(data.runs ?? []).map(run => runBanner(kit, t, run, act))}
      {s.error !== '' && <Text color="error">{s.error}</Text>}
      {s.notice !== '' && <Text color="suggestion">{s.notice}</Text>}
      {spec !== undefined && specView(kit, t, data, spec, s, act, content)}
      {spec === undefined && s.tab === 'register' && registerView(kit, t, data, s, act)}
      {spec === undefined && s.tab === 'register' && data.dirty === true && (
        <Text dimColor size="caption">
          {t.dirty}
        </Text>
      )}
      {spec === undefined && s.tab === 'log' && (
        <Box flexDirection="column" gap="xs">
          <Text dimColor size="caption">
            {t.logNote}
          </Text>
          <Markdown text={logTable(t, data, data.log ?? [])} />
        </Box>
      )}
      {spec === undefined && s.tab === 'config' && <Markdown text={content ?? t.unreadable} />}
    </Box>
  )
}

/** The band of a project docket is not set up in: one line, the button that starts `/docket:init`, and a way out. */
export const drawBand = (kit: Pick<Kit, 'Box' | 'Text' | 'Button'>, t: Words, act: BandActions) => {
  const { Box, Button, Text } = kit

  return (
    <Box flexDirection="row" gap="sm" flexWrap="wrap" alignItems="center">
      <Text dimColor>{t.bandText}</Text>
      <Button key="docket-init" variant="primary" label={t.bandInit} onPress={act.init} />
      <Button key="docket-hide" label={t.bandHide} onPress={act.hide} />
    </Box>
  )
}
