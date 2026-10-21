/**
 * 网盘同步日志页 - 日志管理
 *
 * 每次网盘互传任务（网盘管理页「同步到X」按钮）结束落一条记录：
 * - 成功/失败、方向、路径、文件级统计（上传/跳过/失败）、单文件错误明细
 * - 运行中的任务不在这里，以网盘管理页按钮上的「同步中 x/y」为准
 * - 排查入口：本页错误明细 > backend-web/logs/backend-web.log 过滤 [pan-sync]
 */
import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Cloud,
  Loader2,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { clearPanSyncLogs, getPanSyncLogs, type PanSyncLogItem } from '@/api/panSyncLogs'
import { PageLoading } from '@/components/common/Loading'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'

const PAN_LABELS: Record<string, string> = {
  baidu: '百度',
  quark: '夸克',
}

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

function panLabel(panType: string) {
  return PAN_LABELS[panType] || panType
}

function formatDuration(seconds: number | null) {
  if (seconds === null || seconds === undefined) return '-'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  return `${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s`
}

function formatTime(iso: string | null) {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/** 解析 errors（JSON 字符串数组） */
function parseErrors(raw: string | null): string[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.map(String) : [String(parsed)]
  } catch {
    return [raw]
  }
}

export function PanSyncLogs() {
  const { addToast } = useUIStore()
  const [logs, setLogs] = useState<PanSyncLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [statusFilter, setStatusFilter] = useState('')
  const [panFilter, setPanFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [initialized, setInitialized] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const loadLogs = useCallback(
    async (targetPage: number, targetPageSize: number) => {
      setLoading(true)
      try {
        const res = await getPanSyncLogs({
          status: statusFilter || undefined,
          pan_type: panFilter || undefined,
          page: targetPage,
          page_size: targetPageSize,
        })
        if (res.success && res.data) {
          setLogs(res.data.list)
          setTotal(res.data.total)
          setPage(res.data.page)
        } else {
          addToast({ type: 'error', message: res.message || '加载同步日志失败' })
        }
      } catch (error) {
        addToast({ type: 'error', message: getApiErrorMessage(error, '加载同步日志失败') })
      } finally {
        setLoading(false)
        setInitialized(true)
      }
    },
    [addToast, statusFilter, panFilter]
  )

  useEffect(() => {
    setPage(1)
    loadLogs(1, pageSize)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, panFilter])

  useEffect(() => {
    if (initialized) loadLogs(page, pageSize)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize])

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const handleClear = async () => {
    if (!window.confirm(`确定清空全部 ${total} 条网盘同步日志吗？此操作不可恢复。`)) return
    try {
      const res = await clearPanSyncLogs()
      if (res.success) {
        addToast({ type: 'success', message: res.message || '已清空' })
        setPage(1)
        loadLogs(1, pageSize)
      } else {
        addToast({ type: 'error', message: res.message || '清空失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '清空失败') })
    }
  }

  if (!initialized) {
    return <PageLoading />
  }

  return (
    <div className="space-y-4">
      <div className="page-header flex-between">
        <div>
          <h1 className="page-title">网盘同步日志</h1>
          <p className="page-description">
            网盘互传（同步到对端盘）任务的历史记录：方向、路径、文件级统计与失败明细；
            运行中的任务请看网盘管理页的按钮进度
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => loadLogs(page, pageSize)} className="btn-ios-secondary" disabled={loading}>
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
          <button onClick={handleClear} className="btn-ios-secondary" disabled={loading || total === 0}>
            <Trash2 className="w-4 h-4" />
            清空
          </button>
        </div>
      </div>

      {/* 筛选 */}
      <div className="vben-card">
        <div className="vben-card-body flex flex-wrap items-center gap-3 py-3">
          <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            状态
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="input-ios !w-32 !py-1.5"
            >
              <option value="">全部</option>
              <option value="done">完成</option>
              <option value="failed">失败</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            网盘
            <select
              value={panFilter}
              onChange={(e) => setPanFilter(e.target.value)}
              className="input-ios !w-36 !py-1.5"
            >
              <option value="">全部</option>
              <option value="baidu">百度</option>
              <option value="quark">夸克</option>
            </select>
          </label>
          <span className="text-xs text-slate-400">共 {total} 条记录</span>
        </div>
      </div>

      <div className="vben-card">
        <div className="vben-card-header flex-between">
          <h2 className="vben-card-title">
            <Cloud className="w-4 h-4 text-blue-500" />
            同步任务记录
          </h2>
        </div>
        <div className="table-scroll">
          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
            </div>
          ) : logs.length === 0 ? (
            <div className="empty-state py-8">
              <p className="text-slate-500 dark:text-slate-400">暂无同步记录</p>
            </div>
          ) : (
            <table className="table-ios min-w-[1000px]">
              <thead className="sticky top-0 bg-white dark:bg-slate-800 z-[1]">
                <tr>
                  <th>时间</th>
                  <th>方向</th>
                  <th>路径</th>
                  <th>类型</th>
                  <th>结果</th>
                  <th>文件统计</th>
                  <th>耗时</th>
                  <th>错误明细</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => {
                  const errors = parseErrors(log.errors)
                  const failed = log.failed > 0 || log.status === 'failed'
                  return (
                    <tr key={log.id}>
                      <td className="whitespace-nowrap text-xs">{formatTime(log.created_at)}</td>
                      <td className="whitespace-nowrap text-xs">
                        {panLabel(log.src_type)} → {panLabel(log.dst_type)}
                      </td>
                      <td className="min-w-[260px] max-w-[380px]">
                        <span className="break-all text-xs">{log.remote}</span>
                      </td>
                      <td className="whitespace-nowrap text-xs">
                        {log.is_dir ? '文件夹' : '文件'}
                      </td>
                      <td className="whitespace-nowrap">
                        {log.status === 'done' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400">
                            <CheckCircle2 className="w-3 h-3" />
                            完成
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400">
                            <AlertTriangle className="w-3 h-3" />
                            失败
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap text-xs">
                        共 {log.total} · 传 <span className="text-green-600 dark:text-green-400">{log.uploaded}</span>
                        {' '}· 跳 <span className="text-slate-400">{log.skipped}</span>
                        {' '}·{' '}
                        <span className={failed ? 'text-red-600 dark:text-red-400 font-medium' : 'text-slate-400'}>
                          败 {log.failed}
                        </span>
                      </td>
                      <td className="whitespace-nowrap text-xs">{formatDuration(log.duration_seconds)}</td>
                      <td className="min-w-[160px]">
                        {errors.length === 0 ? (
                          <span className="text-xs text-slate-400">-</span>
                        ) : (
                          <div className="space-y-1">
                            <button
                              onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
                              className="inline-flex items-center gap-1 text-xs text-red-600 dark:text-red-400 hover:underline"
                            >
                              <AlertTriangle className="w-3 h-3" />
                              {errors.length} 条错误{expandedId === log.id ? '（收起）' : '（展开）'}
                            </button>
                            {expandedId === log.id && (
                              <ul className="space-y-0.5 pl-1">
                                {errors.map((err, i) => (
                                  <li key={i} className="text-xs text-red-600 dark:text-red-400 break-all">
                                    {err}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* 分页 */}
        <div className="vben-card-footer flex items-center justify-between gap-2 flex-wrap">
          <label className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
            每页
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value))
                setPage(1)
              }}
              className="input-ios !w-20 !py-1"
            >
              {PAGE_SIZE_OPTIONS.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
            条
          </label>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
              className="btn-ios-secondary !px-2 !py-1"
            >
              <ChevronLeft className="w-4 h-4" />
              上一页
            </button>
            <span className="text-sm text-slate-500 dark:text-slate-400">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || loading}
              className="btn-ios-secondary !px-2 !py-1"
            >
              下一页
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
