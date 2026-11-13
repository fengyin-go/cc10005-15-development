import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/delta'

const columns = ["游标编号", "目标站点", "增量字段", "水位类型", "当前水位", "回退步数", "批次大小", "去重窗口"]
const NEXT_ACTIONS: Record<string, string[]> = {
  '草稿': ['提交校验'],
  '待校验': ['启动增量'],
  '运行中': ['暂停增量'],
  '已暂停': ['续做增量'],
}
const WATERMARK_TYPES = ['数值', '时间', '有序文本']

type Row = Record<string, any>

type Segment = {
  kind: string
  track: string
  start: string
  end: string
  x0: number
  x1: number
  job: string
  批次号?: string
}

type Claim = {
  id: number
  track: string
  start: string
  end: string
  x0: number
  x1: number
  status: string
  job: string
}

type Sample = {
  样本键: string
  水位: string
  x: number
  track: string
  批次号: string
  判定: string
}

type Intervals = {
  cursor: Row
  可比较: boolean
  不可比较原因: string | null
  预留前沿: string | null
  前沿坐标: number | null
  tracks: {
    realtime: { confirmed: Segment[]; claims: Claim[] }
    backfill: { coverage: Segment[]; claims: Claim[] }
  }
  samples: Sample[]
  axis: { min: number; max: number }
}

type Win = { start: number; end: number }

type DragState = {
  mode: 'move' | 'left' | 'right'
  pointerId: number
  startClientX: number
  orig: Win
}

function defaultWindow(data: Intervals): Win {
  const { min, max } = data.axis
  const span = max - min || 1
  const frontier = data.前沿坐标 ?? max
  return {
    start: Math.max(min, frontier - span * 0.4),
    end: Math.min(max, frontier + span * 0.12),
  }
}

function formatTick(x: number, cursor: Row | undefined): string {
  const type = cursor?.['水位类型']
  if (type === '时间') {
    return new Date(x * 86400_000).toISOString().slice(0, 10)
  }
  if (type === '有序文本') {
    const order: string[] = cursor?.['水位顺序表'] ?? []
    return order[Math.round(x)] ?? String(Math.round(x))
  }
  return String(Math.round(x * 100) / 100)
}

function segClass(status: string, track: string): string {
  if (status === '已暂停') return 'seg seg-paused'
  if (status === '在途') return track === 'backfill' ? 'seg seg-backfill-inflight' : 'seg seg-inflight'
  return 'seg seg-confirmed'
}

export default function DeltaView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [intervals, setIntervals] = useState<Intervals | null>(null)
  const [win, setWin] = useState<Win | null>(null)

  const [showCreate, setShowCreate] = useState(false)
  const [createForm, setCreateForm] = useState<Record<string, string>>({
    '游标编号': '', '目标站点': '', '增量字段': '', '水位类型': '数值',
    '当前水位': '', '回退步数': '100', '批次大小': '100', '去重窗口': '50', '重跑开关': '关',
  })
  const [wmForm, setWmForm] = useState({ '水位类型': '数值', '当前水位': '', '水位顺序表': '' })
  const [claimForm, setClaimForm] = useState({ track: 'realtime', start: '', end: '', job: '' })
  const [receiptFor, setReceiptFor] = useState<number | null>(null)
  const [receiptForm, setReceiptForm] = useState({ '批次号': '', '实际终点': '', '样本': '' })

  const stageRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<DragState | null>(null)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (statusFilter) query.set('status', statusFilter)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) throw new Error('增量游标列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '增量采集列表读取失败')
    }
  }, [keyword, statusFilter])

  const loadIntervals = useCallback(async (id: number) => {
    const response = await request(`${ENDPOINT}/${id}/intervals`)
    if (!response.ok) throw new Error('区间总览读取失败')
    const data = (await response.json()) as Intervals
    setIntervals(data)
    setWin((prev) => prev ?? defaultWindow(data))
  }, [])

  const refreshIntervals = useCallback(async () => {
    if (selectedId == null) return
    try {
      await loadIntervals(selectedId)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '区间总览读取失败')
    }
  }, [selectedId, loadIntervals])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (selectedId != null) void loadIntervals(selectedId)
  }, [selectedId, loadIntervals])

  function selectCursor(row: Row) {
    setSelectedId(Number(row.id))
    setIntervals(null)
    setWin(null)
    setReceiptFor(null)
    setNotice('')
    setErrorMessage('')
  }

  async function postAction(path: string, body: unknown, fallback: string): Promise<boolean> {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}${path}`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? fallback)
        return false
      }
      setNotice(payload.message ?? '操作完成')
      return true
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : fallback)
      return false
    }
  }

  async function runCursorAction(row: Row, action: string) {
    const ok = await postAction(`/${row.id}/actions`, { values: { action } }, '增量采集动作未生效，请稍后重试')
    if (ok) {
      await reload()
      await refreshIntervals()
    }
  }

  async function submitCreate() {
    const ok = await postAction('', { values: createForm }, '增量游标登记失败')
    if (ok) {
      setShowCreate(false)
      await reload()
    }
  }

  async function submitWatermark() {
    if (selectedId == null) return
    const order = wmForm['水位顺序表'].split(/[,，\n]/).map((item) => item.trim()).filter(Boolean)
    const ok = await postAction(`/${selectedId}/watermark`, {
      '水位类型': wmForm['水位类型'],
      '当前水位': wmForm['当前水位'] || undefined,
      '水位顺序表': order,
    }, '水位类型设置失败')
    if (ok) await refreshIntervals()
  }

  async function submitClaim() {
    if (selectedId == null) return
    const ok = await postAction(`/${selectedId}/claims`, {
      track: claimForm.track,
      start: claimForm.start || undefined,
      end: claimForm.end || undefined,
      job: claimForm.job || undefined,
    }, '区间认领失败')
    if (ok) {
      setClaimForm((prev) => ({ ...prev, start: '', end: '', job: '' }))
      await refreshIntervals()
    }
  }

  async function submitReceipt(claim: Claim) {
    if (selectedId == null) return
    const samples = receiptForm['样本']
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const at = line.lastIndexOf('@')
        return at > 0
          ? { '样本键': line.slice(0, at).trim(), '水位': line.slice(at + 1).trim() }
          : { '样本键': line, '水位': '' }
      })
      .filter((item) => item['样本键'] && item['水位'])
    const ok = await postAction(`/${selectedId}/claims/${claim.id}/confirm`, {
      '批次号': receiptForm['批次号'],
      '实际终点': receiptForm['实际终点'] || undefined,
      '样本': samples,
    }, '批次回执确认失败')
    if (ok) {
      setReceiptFor(null)
      await reload()
      await refreshIntervals()
    }
  }

  // ------------------------------------------------------------ 观察窗口拖拽
  function beginDrag(mode: DragState['mode'], event: ReactPointerEvent) {
    if (!win) return
    dragRef.current = { mode, pointerId: event.pointerId, startClientX: event.clientX, orig: win }
    event.currentTarget.setPointerCapture(event.pointerId)
    event.stopPropagation()
  }

  function onDragMove(event: ReactPointerEvent) {
    const drag = dragRef.current
    const stage = stageRef.current
    if (!drag || !stage || !intervals || event.pointerId !== drag.pointerId) return
    const rect = stage.getBoundingClientRect()
    if (rect.width <= 0) return
    const { min, max } = intervals.axis
    const axisSpan = max - min || 1
    const dx = ((event.clientX - drag.startClientX) / rect.width) * axisSpan
    const minWidth = Math.max(axisSpan / 200, 1e-6)
    let { start, end } = drag.orig
    if (drag.mode === 'move') {
      const span = drag.orig.end - drag.orig.start
      start = drag.orig.start + dx
      end = drag.orig.end + dx
      if (start < min) { start = min; end = start + span }
      if (end > max) { end = max; start = end - span }
    } else if (drag.mode === 'left') {
      start = Math.min(Math.max(drag.orig.start + dx, min), drag.orig.end - minWidth)
    } else {
      end = Math.max(Math.min(drag.orig.end + dx, max), drag.orig.start + minWidth)
    }
    setWin({ start, end })
  }

  function endDrag(event: ReactPointerEvent) {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null
  }

  // ------------------------------------------------------------ 派生数据
  const axis = intervals?.axis ?? { min: 0, max: 1 }
  const axisSpan = axis.max - axis.min || 1
  const pct = useCallback((x: number) => ((x - axis.min) / axisSpan) * 100, [axis.min, axisSpan])

  const windowSegments = useMemo(() => {
    if (!intervals || !win) return []
    type WinSegment = { kind: string; track: string; start: string; end: string; x0: number; x1: number; job: string; 段类型: string; 状态: string; 批次号?: string }
    const segs: WinSegment[] = [
      ...intervals.tracks.realtime.confirmed.map((s) => ({ ...s, 段类型: '已确认段', 状态: '已确认' })),
      ...intervals.tracks.realtime.claims.map((c) => ({ ...c, kind: '实时认领', 段类型: c.status === '已暂停' ? '已暂停认领' : '在途认领', 状态: c.status })),
      ...intervals.tracks.backfill.coverage.map((s) => ({ ...s, 段类型: '回扫覆盖', 状态: '已确认' })),
      ...intervals.tracks.backfill.claims.map((c) => ({ ...c, kind: '回扫认领', 段类型: c.status === '已暂停' ? '已暂停回扫' : '在途回扫', 状态: c.status })),
    ]
    return segs.filter((s) => s.x0 < win.end && win.start < s.x1)
  }, [intervals, win])

  const windowSamples = useMemo(() => {
    if (!intervals || !win) return []
    return intervals.samples.filter((s) => s.x >= win.start && s.x <= win.end).slice(-40)
  }, [intervals, win])

  const activeClaims = useMemo(() => {
    if (!intervals) return []
    return [...intervals.tracks.realtime.claims, ...intervals.tracks.backfill.claims]
      .filter((c) => c.status !== '已确认')
  }, [intervals])

  const stats = useMemo(() => ([
    { label: '增量游标总数', value: total },
    { label: '运行中游标', value: rows.filter((row) => row.status === '运行中').length },
    { label: '在途认领', value: intervals ? activeClaims.filter((c) => c.status === '在途').length : '—' },
    { label: '已确认段', value: intervals ? intervals.tracks.realtime.confirmed.length + intervals.tracks.backfill.coverage.length : '—' },
  ]), [total, rows, intervals, activeClaims])

  const ticks = useMemo(() => [0, 0.25, 0.5, 0.75, 1].map((r) => axis.min + axisSpan * r), [axis.min, axisSpan])

  return (
    <section className="page" data-module="delta">
      <header className="page-head">
        <div>
          <h2>增量采集管理</h2>
          <p className="page-desc">
            以双轨区间条呈现实时取数与回扫取数：先认领起止水位区间，采集完成后凭批次回执确认；
            未确认的区间保持在途，其他作业不得穿越推进水位。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setShowCreate((prev) => !prev)}>
            登记增量游标
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出增量采集清单
          </button>
        </div>
      </header>

      {showCreate && (
        <form className="inline-form" onSubmit={(event) => { event.preventDefault(); void submitCreate() }}>
          {['游标编号', '目标站点', '增量字段', '当前水位', '回退步数', '批次大小', '去重窗口'].map((field) => (
            <label key={field}>
              <span>{field}</span>
              <input
                value={createForm[field] ?? ''}
                onChange={(event) => setCreateForm((prev) => ({ ...prev, [field]: event.target.value }))}
              />
            </label>
          ))}
          <label>
            <span>水位类型</span>
            <select
              value={createForm['水位类型']}
              onChange={(event) => setCreateForm((prev) => ({ ...prev, '水位类型': event.target.value }))}
            >
              {WATERMARK_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
            </select>
          </label>
          <button className="btn primary" type="submit">提交登记</button>
        </form>
      )}

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>游标编号</span>
          <input value={keyword} placeholder="按游标编号检索" onChange={(event) => setKeyword(event.target.value)} />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">全部</option>
            {['草稿', '待校验', '运行中', '已暂停'].map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setKeyword(''); setStatusFilter('') }}>重置条件</button>
      </form>

      <table className="data-table clickable">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={String(row.id)}
              className={selectedId === row.id ? 'selected-row' : ''}
              onClick={() => selectCursor(row)}
            >
              {columns.map((column) => <td key={column}>{row[column] || '—'}</td>)}
              <td>{row.status}</td>
              <td className="row-actions" onClick={(event) => event.stopPropagation()}>
                {(NEXT_ACTIONS[String(row.status)] ?? []).map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runCursorAction(row, action)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无增量采集数据，可先登记增量游标</td>
            </tr>
          )}
        </tbody>
      </table>

      {intervals && (
        <div className="interval-panel">
          <h3 className="panel-title">
            双轨区间条：{intervals.cursor['游标编号']}（{intervals.cursor['目标站点']}）
          </h3>
          <div className="interval-meta">
            <span>当前水位 <strong>{intervals.cursor['当前水位'] ?? '—'}</strong></span>
            <span>水位类型 <strong>{intervals.cursor['水位类型'] || '未明确'}</strong></span>
            <span>预留前沿 <strong>{intervals['预留前沿'] ?? '—'}</strong></span>
            <span>回退步数 <strong>{intervals.cursor['回退步数'] ?? '—'}</strong></span>
            <span>去重窗口 <strong>{intervals.cursor['去重窗口'] ?? '—'}</strong></span>
            <span>批次大小 <strong>{intervals.cursor['批次大小'] ?? '—'}</strong></span>
          </div>

          {!intervals['可比较'] && (
            <div className="banner-warn">
              <p>{intervals['不可比较原因']}</p>
              <form className="inline-form" onSubmit={(event) => { event.preventDefault(); void submitWatermark() }}>
                <label>
                  <span>水位类型</span>
                  <select
                    value={wmForm['水位类型']}
                    onChange={(event) => setWmForm((prev) => ({ ...prev, '水位类型': event.target.value }))}
                  >
                    {WATERMARK_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
                  </select>
                </label>
                <label>
                  <span>当前水位（留空则不修改）</span>
                  <input
                    value={wmForm['当前水位']}
                    placeholder={String(intervals.cursor['当前水位'] ?? '')}
                    onChange={(event) => setWmForm((prev) => ({ ...prev, '当前水位': event.target.value }))}
                  />
                </label>
                {wmForm['水位类型'] === '有序文本' && (
                  <label>
                    <span>水位顺序表（逗号分隔，靠前者小）</span>
                    <input
                      value={wmForm['水位顺序表']}
                      placeholder="批次-甲,批次-乙,批次-丙"
                      onChange={(event) => setWmForm((prev) => ({ ...prev, '水位顺序表': event.target.value }))}
                    />
                  </label>
                )}
                <button className="btn primary" type="submit">明确类型与顺序</button>
              </form>
            </div>
          )}

          {intervals['可比较'] && win && (
            <>
              <div className="legend-row">
                <span className="legend-chip"><i className="legend-dot" style={{ background: '#16a34a' }} />已确认段</span>
                <span className="legend-chip"><i className="legend-dot" style={{ background: '#2563eb' }} />在途认领</span>
                <span className="legend-chip"><i className="legend-dot" style={{ background: '#d97706' }} />已暂停</span>
                <span className="legend-chip"><i className="legend-dot" style={{ background: '#7c3aed' }} />回扫覆盖</span>
                <span className="legend-chip"><i className="legend-dot" style={{ background: '#a78bfa' }} />在途回扫</span>
                <span className="legend-chip"><i className="legend-dot" style={{ background: 'transparent', borderLeft: '2px dashed #dc2626', borderRadius: 0 }} />预留前沿</span>
                <span className="legend-chip">拖动蓝色观察窗口框检查各段，左右边缘可缩放</span>
                <button className="link" type="button" onClick={() => setWin(defaultWindow(intervals))}>重置窗口</button>
              </div>

              <div className="track-wrap">
                <div className="track-side">
                  <span>实时取数轨</span>
                  <span>回扫取数轨</span>
                </div>
                <div className="track-stage" ref={stageRef}>
                  <div className="track-row">
                    {intervals.tracks.realtime.confirmed.map((seg, index) => (
                      <div
                        key={`c-${index}`}
                        className="seg seg-confirmed"
                        style={{ left: `${pct(seg.x0)}%`, width: `${Math.max(pct(seg.x1) - pct(seg.x0), 0.4)}%` }}
                        title={`已确认 [${seg.start}, ${seg.end}) 作业 ${seg.job} 批次 ${seg['批次号'] ?? '—'}`}
                      />
                    ))}
                    {intervals.tracks.realtime.claims.map((claim) => (
                      <div
                        key={`rc-${claim.id}`}
                        className={segClass(claim.status, 'realtime')}
                        style={{ left: `${pct(claim.x0)}%`, width: `${Math.max(pct(claim.x1) - pct(claim.x0), 0.4)}%` }}
                        title={`${claim.status}认领 [${claim.start}, ${claim.end}) 作业 ${claim.job}`}
                      />
                    ))}
                  </div>
                  <div className="track-row">
                    {intervals.tracks.backfill.coverage.map((seg, index) => (
                      <div
                        key={`b-${index}`}
                        className="seg seg-coverage"
                        style={{ left: `${pct(seg.x0)}%`, width: `${Math.max(pct(seg.x1) - pct(seg.x0), 0.4)}%` }}
                        title={`回扫覆盖 [${seg.start}, ${seg.end}) 作业 ${seg.job} 批次 ${seg['批次号'] ?? '—'}`}
                      />
                    ))}
                    {intervals.tracks.backfill.claims.map((claim) => (
                      <div
                        key={`bc-${claim.id}`}
                        className={segClass(claim.status, 'backfill')}
                        style={{ left: `${pct(claim.x0)}%`, width: `${Math.max(pct(claim.x1) - pct(claim.x0), 0.4)}%` }}
                        title={`${claim.status}回扫 [${claim.start}, ${claim.end}) 作业 ${claim.job}`}
                      />
                    ))}
                  </div>

                  <div className="win-dim" style={{ left: 0, width: `${pct(win.start)}%` }} />
                  <div className="win-dim" style={{ left: `${pct(win.end)}%`, right: 0 }} />
                  <div
                    className="win-frame"
                    style={{ left: `${pct(win.start)}%`, width: `${pct(win.end) - pct(win.start)}%` }}
                    onPointerDown={(event) => beginDrag('move', event)}
                    onPointerMove={onDragMove}
                    onPointerUp={endDrag}
                  >
                    <div
                      className="win-handle left"
                      onPointerDown={(event) => beginDrag('left', event)}
                      onPointerMove={onDragMove}
                      onPointerUp={endDrag}
                    />
                    <div
                      className="win-handle right"
                      onPointerDown={(event) => beginDrag('right', event)}
                      onPointerMove={onDragMove}
                      onPointerUp={endDrag}
                    />
                  </div>

                  {intervals['前沿坐标'] != null && (
                    <div className="frontier-line" style={{ left: `${pct(intervals['前沿坐标'])}%` }}>
                      <span className="frontier-tag">预留前沿 {intervals['预留前沿']}</span>
                    </div>
                  )}
                </div>
              </div>
              <div className="track-ticks">
                {ticks.map((tick, index) => (
                  <span key={index} style={{ left: `${pct(tick)}%` }}>{formatTick(tick, intervals.cursor)}</span>
                ))}
              </div>

              <h4 className="panel-title">观察窗口内的分段（含关联作业）</h4>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>段类型</th><th>区间起点</th><th>区间终点</th><th>关联作业</th><th>批次号</th>
                  </tr>
                </thead>
                <tbody>
                  {windowSegments.map((seg, index) => (
                    <tr key={index}>
                      <td>{seg['段类型']}</td>
                      <td>{seg.start}</td>
                      <td>{seg.end}</td>
                      <td>{seg.job}</td>
                      <td>{seg['批次号'] ?? '—'}</td>
                    </tr>
                  ))}
                  {windowSegments.length === 0 && (
                    <tr><td className="empty-state" colSpan={5}>观察窗口内暂无分段，拖动窗口检查其他区间</td></tr>
                  )}
                </tbody>
              </table>

              <h4 className="panel-title">认领新区间</h4>
              <form className="inline-form" onSubmit={(event) => { event.preventDefault(); void submitClaim() }}>
                <label>
                  <span>取数轨道</span>
                  <select
                    value={claimForm.track}
                    onChange={(event) => setClaimForm((prev) => ({ ...prev, track: event.target.value }))}
                  >
                    <option value="realtime">实时取数</option>
                    <option value="backfill">回扫取数</option>
                  </select>
                </label>
                {claimForm.track === 'realtime' ? (
                  <label>
                    <span>起点（默认预留前沿 {intervals['预留前沿']}）</span>
                    <input
                      value={claimForm.start}
                      placeholder={intervals['预留前沿'] ?? ''}
                      onChange={(event) => setClaimForm((prev) => ({ ...prev, start: event.target.value }))}
                    />
                  </label>
                ) : (
                  <label>
                    <span>起点（自动：当前水位 − 回退步数）</span>
                    <input value="由回退步数确定" disabled />
                  </label>
                )}
                <label>
                  <span>{claimForm.track === 'realtime' ? '候选终点' : '回扫终点（默认当前水位）'}</span>
                  <input
                    value={claimForm.end}
                    placeholder={claimForm.track === 'backfill' ? String(intervals.cursor['当前水位'] ?? '') : ''}
                    onChange={(event) => setClaimForm((prev) => ({ ...prev, end: event.target.value }))}
                  />
                </label>
                <label>
                  <span>作业号（留空自动生成）</span>
                  <input
                    value={claimForm.job}
                    onChange={(event) => setClaimForm((prev) => ({ ...prev, job: event.target.value }))}
                  />
                </label>
                <button className="btn primary" type="submit">认领区间</button>
              </form>

              <h4 className="panel-title">在途 / 已暂停认领</h4>
              <div className="claim-grid">
                {activeClaims.map((claim) => (
                  <article key={`${claim.track}-${claim.id}`} className="claim-card">
                    <h4>
                      [{claim.start}, {claim.end}){' '}
                      <span className={`chip ${claim.status === '在途' ? 'chip-inflight' : 'chip-paused'}`}>{claim.status}</span>
                    </h4>
                    <p>轨道：{claim.track === 'realtime' ? '实时取数' : '回扫取数'}　作业：{claim.job}</p>
                    <div className="row-actions">
                      {claim.status === '在途' && (
                        <>
                          <button
                            className="link"
                            type="button"
                            onClick={() => void postAction(`/${selectedId}/claims/${claim.id}/pause`, {}, '暂停失败').then((ok) => { if (ok) void refreshIntervals() })}
                          >
                            暂停
                          </button>
                          <button
                            className="link"
                            type="button"
                            onClick={() => {
                              setReceiptFor(receiptFor === claim.id ? null : claim.id)
                              setReceiptForm({
                                '批次号': `BATCH-${intervals.cursor['游标编号']}-${claim.id}`,
                                '实际终点': claim.end,
                                '样本': '',
                              })
                            }}
                          >
                            批次回执确认
                          </button>
                        </>
                      )}
                      {claim.status === '已暂停' && (
                        <button
                          className="link"
                          type="button"
                          onClick={() => void postAction(`/${selectedId}/claims/${claim.id}/resume`, {}, '续做失败').then((ok) => { if (ok) void refreshIntervals() })}
                        >
                          续做（从该段接着取数）
                        </button>
                      )}
                    </div>
                    {receiptFor === claim.id && (
                      <form className="inline-form" onSubmit={(event) => { event.preventDefault(); void submitReceipt(claim) }}>
                        <label>
                          <span>批次号</span>
                          <input
                            value={receiptForm['批次号']}
                            onChange={(event) => setReceiptForm((prev) => ({ ...prev, '批次号': event.target.value }))}
                          />
                        </label>
                        <label>
                          <span>实际终点</span>
                          <input
                            value={receiptForm['实际终点']}
                            onChange={(event) => setReceiptForm((prev) => ({ ...prev, '实际终点': event.target.value }))}
                          />
                        </label>
                        <label>
                          <span>样本（可选，每行一条：样本键@水位，含迟到样本）</span>
                          <textarea
                            rows={2}
                            value={receiptForm['样本']}
                            placeholder={'迟到-1@' + claim.start}
                            onChange={(event) => setReceiptForm((prev) => ({ ...prev, '样本': event.target.value }))}
                          />
                        </label>
                        <button className="btn primary" type="submit">提交回执</button>
                      </form>
                    )}
                  </article>
                ))}
                {activeClaims.length === 0 && <p className="empty-state">暂无在途认领，可在上方认领新区间</p>}
              </div>

              <h4 className="panel-title">观察窗口内的样本判定（去重窗口跨双轨生效）</h4>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>样本键</th><th>水位</th><th>轨道</th><th>批次号</th><th>判定</th>
                  </tr>
                </thead>
                <tbody>
                  {windowSamples.map((sample, index) => (
                    <tr key={index}>
                      <td>{sample['样本键']}</td>
                      <td>{sample['水位']}</td>
                      <td>{sample.track === 'realtime' ? '实时' : '回扫'}</td>
                      <td>{sample['批次号']}</td>
                      <td>
                        <span className={`chip ${sample['判定'] === '重复' ? 'chip-dup' : sample['判定'] === '迟到补录' ? 'chip-late' : 'chip-normal'}`}>
                          {sample['判定']}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {windowSamples.length === 0 && (
                    <tr><td className="empty-state" colSpan={5}>观察窗口内暂无样本，确认批次回执后此处出现判定结果</td></tr>
                  )}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条增量采集记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
