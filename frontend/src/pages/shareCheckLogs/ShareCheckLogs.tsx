/**
 * 分享续期巡检日志页 - 日志管理
 *
 * 数据来源 xy_share_check_logs：
 * - 续期（renew）：到期自动续期/手动重新分享的逐条结果，含新旧地址
 * - 巡检（check）：内容存活巡检逐条结果（真实浏览器打开分享页判定）
 *   valid=内容存在 / invalid=链接失效 / empty=页面可开但无文件 /
 *   error=打不开或超时（不等于失效，会重试） / risk=疑似风控登录墙
 * - 排查入口：本页详情 > backend-web/logs 过滤 [share-renew] / 巡检子进程 stderr
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
import { clearShareCheckLogs, getShareCheckLogs, type ShareCheckLogItem } from '@/api/shareCheckLogs'
import { PageLoading } from '@/components/common/Loading'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'

const PAN_LABELS: Record<string, string> = {
  baidu: '百度',
  quark: '夸克',
}

const TASK_TYPE_LABELS: Record<string, string> = {
  renew: '续期',
  check: '巡检',
}

const RESULT_META: Record<string, { label: string; className: string }> = {
  done: { label: '完成', className: 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400' },
  failed: { label: '失败', className: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400' },
  valid: { label: '内容存在', className: 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400' },
  invalid: { label: '链接失效', className: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400' },
  empty: { label: '内容为空', className: 'bg-orange-50 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' },
  error: { label: '检查异常', className: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300' },
  risk: { label: '风控/登录墙', className: 'bg-purple-50 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400' },
}

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

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

export function ShareCheckLogs() {
  const { addToast } = useUIStore()
  const [logs, setLogs] = useState<ShareCheckLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [taskTypeFilter, setTaskTypeFilter] = useState('')
  const [resultFilter, setResultFilter] = useState('')
  const [panFilter, setPanFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [initialized, setInitialized] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const loadLogs = useCallback(
    async (targetPage: number, targetPageSize: number) => {
      setLoading(true)
      try {
        const res = await getShareCheckLogs({
          task_type: taskTypeFilter || undefined,
          result: resultFilter || undefined,
          pan_type: panFilter || undefined,
          page: targetPage,
          page_size: targetPageSize,
        })
        if (res.success && res.data) {
          setLogs(res.data.list)
          setTotal(res.data.total)
          setPage(res.data.page)
        } else {
          addToast({ type: 'error', message: res.message || '加载日志失败' })
        }
      } catch (error) {
        addToast({ type: 'error', message: getApiErrorMessage(error, '加载日志失败') })
      } finally {
        setLoading(false)
        setInitialized(true)
      }
    },
    [addToast, taskTypeFilter, resultFilter, panFilter]
  )

  useEffect(() => {
    setPage(1)
    loadLogs(1, pageSize)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskTypeFilter, resultFilter, panFilter])

  useEffect(() => {
    if (initialized) loadLogs(page, pageSize)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize])

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const handleClear = async () => {
    if (!window.confirm(`确定清空全部 ${total} 条分享续期巡检日志吗？此操作不可恢复。`)) return
    try {
      const res = await clearShareCheckLogs()
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
          <h1 className="page-title">分享续期巡检日志</h1>
          <p className="page-description">
            网盘分享到期自动续期与内容存活巡检的逐条记录：续期含新旧地址对比，
            巡检为真实浏览器打开分享页的判定结果（失效/为空/异常需人工到资料池点「重新分享」）
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
            类型
            <select
              value={taskTypeFilter}
              onChange={(e) => setTaskTypeFilter(e.target.value)}
              className="input-ios !w-32 !py-1.5"
            >
              <option value="">全部</option>
              <option value="renew">续期</option>
              <option value="check">巡检</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            结果
            <select
              value={resultFilter}
              onChange={(e) => setResultFilter(e.target.value)}
              className="input-ios !w-36 !py-1.5"
            >
              <option value="">全部</option>
              {Object.entries(RESULT_META).map(([value, meta]) => (
                <option key={value} value={value}>
                  {meta.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            网盘
            <select
              value={panFilter}
              onChange={(e) => setPanFilter(e.target.value)}
              className="input-ios !w-32 !py-1.5"
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
            续期 / 巡检记录
          </h2>
        </div>
        <div className="table-scroll">
          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
            </div>
          ) : logs.length === 0 ? (
            <div className="empty-state py-8">
              <p className="text-slate-500 dark:text-slate-400">暂无记录</p>
            </div>
          ) : (
            <table className="table-ios min-w-[1000px]">
              <thead className="sticky top-0 bg-white dark:bg-slate-800 z-[1]">
                <tr>
                  <th>时间</th>
                  <th>类型</th>
                  <th>网盘</th>
                  <th>结果</th>
                  <th>地址</th>
                  <th>耗时</th>
                  <th>详情</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => {
                  const meta = RESULT_META[log.result] || {
                    label: log.result,
                    className: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300',
                  }
                  const abnormal = ['failed', 'invalid', 'empty', 'risk'].includes(log.result)
                  return (
                    <tr key={log.id}>
                      <td className="whitespace-nowrap text-xs">{formatTime(log.created_at)}</td>
                      <td className="whitespace-nowrap text-xs">
                        {TASK_TYPE_LABELS[log.task_type] || log.task_type}
                      </td>
                      <td className="whitespace-nowrap text-xs">{PAN_LABELS[log.pan_type] || log.pan_type}</td>
                      <td className="whitespace-nowrap">
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${meta.className}`}
                        >
                          {abnormal ? <AlertTriangle className="w-3 h-3" /> : <CheckCircle2 className="w-3 h-3" />}
                          {meta.label}
                        </span>
                      </td>
                      <td className="min-w-[220px] max-w-[340px]">
                        {log.pan_url ? (
                          <div className="space-y-0.5">
                            <span className="break-all text-xs">{log.pan_url}</span>
                            {log.old_url && log.old_url !== log.pan_url && (
                              <div className="break-all text-xs text-slate-400 line-through">{log.old_url}</div>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-slate-400">-</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap text-xs">{formatDuration(log.duration_seconds)}</td>
                      <td className="min-w-[160px] max-w-[320px]">
                        {log.fail_reason || log.detail ? (
                          <div className="space-y-1">
                            <button
                              onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
                              className={`inline-flex items-center gap-1 text-xs hover:underline ${
                                abnormal ? 'text-red-600 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'
                              }`}
                            >
                              <AlertTriangle className="w-3 h-3" />
                              {expandedId === log.id ? '收起' : '查看'}
                            </button>
                            {expandedId === log.id && (
                              <div className="space-y-0.5 pl-1">
                                {log.fail_reason && (
                                  <p className="text-xs text-red-600 dark:text-red-400 break-all">{log.fail_reason}</p>
                                )}
                                {log.detail && (
                                  <p className="text-xs text-slate-500 dark:text-slate-400 break-all">{log.detail}</p>
                                )}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-slate-400">-</span>
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
        {total > 0 && (
          <div className="flex-shrink-0 vben-card-footer flex flex-col sm:flex-row items-center justify-between gap-4 px-4 py-3 border-t border-slate-200 dark:border-slate-700">
            <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
              <span>每页</span>
              <select
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value))
                  setPage(1)
                }}
                className="px-2 py-1 border border-slate-300 dark:border-slate-600 rounded bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300"
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </select>
              <span>条</span>
              <span className="ml-2">
                显示 {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, total)} 条，共 {total} 条
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || loading}
                className="p-2 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-100 dark:hover:bg-slate-700"
                title="上一页"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="px-3 py-1 text-sm text-slate-600 dark:text-slate-400">
                第 {page} / {totalPages} 页
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || loading}
                className="p-2 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-100 dark:hover:bg-slate-700"
                title="下一页"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
