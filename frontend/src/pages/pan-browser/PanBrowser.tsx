/**
 * 网盘管理页面 - 网盘浏览与直选上架
 *
 * 功能：
 * 1. 面包屑 + 单层表格逐层下钻浏览夸克网盘（一层一次接口调用）
 * 2. 文件/文件夹创建分享链接（查重，弹窗显示 + 复制）
 * 3. 选中文件补充信息（标题/售价/描述/封面/学校/年份/年级）一键导入资料池
 * 4. 「资料池状态」列标记已导入文件，辅助人工判断
 *
 * 路径状态写进 URL（?path=），刷新不丢位置；文件夹仅浏览+分享，上架仅文件。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle,
  ArrowUp,
  CheckCircle2,
  ChevronRight,
  Cloud,
  Copy,
  FileText,
  Folder,
  ImagePlus,
  Link2,
  Loader2,
  RefreshCw,
  Rocket,
  Share2,
  X,
} from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import {
  createPanShare,
  getPanEntries,
  getPanStatus,
  importPanFile,
  type PanEntry,
  type PanShareResult,
} from '@/api/panBrowser'
import { updateMaterialPool, uploadMaterialPoolCover } from '@/api/materialPool'
import { PageLoading } from '@/components/common/Loading'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'

const DEFAULT_PATH = '/资料池'

const POOL_STATUS_LABELS: Record<string, string> = {
  crawled: '已采集',
  uploaded: '已传网盘',
  pending: '待审核',
  approved: '审核通过',
  listing: '上架中',
  listed: '在售',
  rejected: '不售',
  failed: '失败',
}

const POOL_STATUS_CLASSES: Record<string, string> = {
  crawled: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300',
  uploaded: 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400',
  pending: 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400',
  approved: 'bg-cyan-50 text-cyan-600 dark:bg-cyan-900/30 dark:text-cyan-400',
  listing: 'bg-indigo-50 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400',
  listed: 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400',
  rejected: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
  failed: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400',
}

function formatFileSize(size?: number | null) {
  if (!size && size !== 0) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatDateTime(seconds?: number | null) {
  if (!seconds) return '-'
  const date = new Date(seconds * 1000)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** 从路径生成默认商品标题：去扩展名 + 截断 30 字（普通卖家账号上限） */
function defaultTitleFromFileName(fileName: string): string {
  const base = fileName.replace(/\.[^.]+$/, '')
  return base.slice(0, 30)
}

// ==================== 弹窗外壳 ====================

function ModalShell({
  isOpen,
  title,
  onClose,
  children,
  width = 'max-w-lg',
}: {
  isOpen: boolean
  title: string
  onClose: () => void
  children: React.ReactNode
  width?: string
}) {
  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            className={`relative w-full ${width} mx-4 max-h-[85vh] overflow-y-auto bg-white dark:bg-slate-800 rounded-2xl shadow-2xl border border-slate-200 dark:border-slate-700`}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700 sticky top-0 bg-white dark:bg-slate-800 z-10">
              <h3 className="text-lg font-semibold text-slate-900 dark:text-white">{title}</h3>
              <button
                onClick={onClose}
                className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-6">{children}</div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}

// ==================== 主页面 ====================

export function PanBrowser() {
  const { addToast } = useUIStore()
  const [searchParams, setSearchParams] = useSearchParams()
  const currentPath = searchParams.get('path') || DEFAULT_PATH

  const [connState, setConnState] = useState<'checking' | 'ok' | 'fail'>('checking')
  const [connMessage, setConnMessage] = useState('')
  const [entries, setEntries] = useState<PanEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [initialized, setInitialized] = useState(false)

  // 弹窗状态
  const [shareEntry, setShareEntry] = useState<PanEntry | null>(null)
  const [shareResult, setShareResult] = useState<PanShareResult | null>(null)
  const [sharing, setSharing] = useState(false)
  const [importEntry, setImportEntry] = useState<PanEntry | null>(null)

  const navigateTo = useCallback(
    (path: string) => {
      setSearchParams({ path })
    },
    [setSearchParams]
  )

  // 面包屑分段：/资料池/学军中学 → [网盘, 资料池, 学军中学]
  const breadcrumbs = useMemo(() => {
    const parts = currentPath.split('/').filter(Boolean)
    const items = [{ label: '网盘', path: '/' }]
    let acc = ''
    for (const part of parts) {
      acc += `/${part}`
      items.push({ label: part, path: acc })
    }
    return items
  }, [currentPath])

  const loadEntries = useCallback(
    async (path: string) => {
      setLoading(true)
      try {
        const res = await getPanEntries(path)
        if (res.success && res.data) {
          setEntries(res.data.list)
        } else {
          setEntries([])
          addToast({ type: 'error', message: res.message || '加载网盘目录失败' })
        }
      } catch (error) {
        setEntries([])
        addToast({ type: 'error', message: getApiErrorMessage(error, '加载网盘目录失败') })
      } finally {
        setLoading(false)
        setInitialized(true)
      }
    },
    [addToast]
  )

  useEffect(() => {
    getPanStatus()
      .then((res) => {
        if (res.success) {
          setConnState('ok')
        } else {
          setConnState('fail')
          setConnMessage(res.message || '网盘连接失败')
        }
      })
      .catch(() => {
        setConnState('fail')
        setConnMessage('网盘状态检查请求失败')
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    loadEntries(currentPath)
  }, [currentPath, loadEntries])

  const handleCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      addToast({ type: 'success', message: '链接已复制' })
    } catch {
      addToast({ type: 'error', message: '复制失败，请手动复制' })
    }
  }

  const handleShare = async () => {
    if (!shareEntry) return
    setSharing(true)
    try {
      const res = await createPanShare(shareEntry.path, shareEntry.fid)
      if (res.success && res.data) {
        setShareResult(res.data)
        loadEntries(currentPath)
      } else {
        addToast({ type: 'error', message: res.message || '创建分享失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '创建分享失败') })
    } finally {
      setSharing(false)
    }
  }

  const sortedEntries = useMemo(() => {
    return [...entries].sort((a, b) => {
      if (a.dir !== b.dir) return a.dir ? -1 : 1
      return a.file_name.localeCompare(b.file_name, 'zh-CN')
    })
  }, [entries])

  if (!initialized) {
    return <PageLoading />
  }

  const parentPath = currentPath === '/' ? null : currentPath.slice(0, currentPath.lastIndexOf('/')) || '/'

  return (
    <div className="space-y-4">
      <div className="page-header flex-between">
        <div>
          <h1 className="page-title">网盘管理</h1>
          <p className="page-description">浏览夸克网盘，分享链接，或把文件补充信息后直选上架到资料池</p>
        </div>
        <button onClick={() => loadEntries(currentPath)} className="btn-ios-secondary" disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {connState === 'checking' && (
        <div className="vben-card">
          <div className="vben-card-body flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
            <Loader2 className="w-4 h-4 animate-spin" />
            正在检测网盘连接状态...
          </div>
        </div>
      )}

      {connState === 'fail' && (
        <div className="vben-card border-red-200 dark:border-red-900">
          <div className="vben-card-body flex items-start gap-2 text-sm text-red-600 dark:text-red-400">
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <div>
              <div className="font-medium">网盘连接失败</div>
              <div className="mt-0.5">{connMessage}</div>
              <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                请检查 kuake_cli/.env 中的 KUAKE_COOKIE 是否过期，以及 KUAKE_BIN 配置是否正确。
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="vben-card flex flex-col" style={{ height: 'calc(100vh - 300px)', minHeight: '420px' }}>
        <div className="vben-card-header flex-shrink-0 flex-between">
          <h2 className="vben-card-title">
            <Cloud className="w-4 h-4 text-blue-500" />
            网盘内容
          </h2>
          <span className="badge-primary">{sortedEntries.length} 个条目</span>
        </div>

        {/* 面包屑 */}
        <div className="flex-shrink-0 flex items-center gap-1 px-4 py-2 border-b border-slate-200 dark:border-slate-700 text-sm overflow-x-auto whitespace-nowrap">
          {breadcrumbs.map((item, index) => (
            <span key={item.path} className="flex items-center gap-1">
              {index > 0 && <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
              <button
                onClick={() => navigateTo(item.path)}
                className={`hover:text-blue-600 dark:hover:text-blue-400 ${
                  index === breadcrumbs.length - 1
                    ? 'text-slate-900 dark:text-white font-medium'
                    : 'text-slate-500 dark:text-slate-400'
                }`}
              >
                {item.label}
              </button>
            </span>
          ))}
        </div>

        <div className="table-scroll">
          {loading ? (
            <div className="flex justify-center py-8">
              <RefreshCw className="w-6 h-6 animate-spin text-blue-500" />
            </div>
          ) : (
            <table className="table-ios min-w-[900px]">
              <thead className="sticky top-0 bg-white dark:bg-slate-800 z-[1]">
                <tr>
                  <th>名称</th>
                  <th>大小</th>
                  <th>修改时间</th>
                  <th>资料池状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {parentPath !== null && (
                  <tr className="cursor-pointer" onClick={() => navigateTo(parentPath)}>
                    <td className="min-w-[320px]">
                      <div className="flex items-center gap-2">
                        <ArrowUp className="w-4 h-4 text-slate-400" />
                        <span className="text-slate-500 dark:text-slate-400">..</span>
                      </div>
                    </td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                  </tr>
                )}
                {sortedEntries.length === 0 ? (
                  <tr>
                    <td colSpan={5}>
                      <div className="empty-state py-8">
                        <p className="text-slate-500 dark:text-slate-400">此文件夹为空</p>
                      </div>
                    </td>
                  </tr>
                ) : (
                  sortedEntries.map((entry) => (
                    <tr key={entry.fid}>
                      <td
                        className={`min-w-[320px] max-w-[480px] ${entry.dir ? 'cursor-pointer' : ''}`}
                        onClick={() => entry.dir && navigateTo(entry.path)}
                      >
                        <div className="flex items-center gap-2">
                          {entry.dir ? (
                            <Folder className="w-4 h-4 text-amber-500 flex-shrink-0" />
                          ) : (
                            <FileText className="w-4 h-4 text-slate-400 flex-shrink-0" />
                          )}
                          <span className="break-all">{entry.file_name}</span>
                        </div>
                      </td>
                      <td className="whitespace-nowrap text-xs">{entry.dir ? '-' : formatFileSize(entry.size)}</td>
                      <td className="whitespace-nowrap text-xs">{formatDateTime(entry.mtime)}</td>
                      <td className="whitespace-nowrap">
                        {entry.pool ? (
                          <span
                            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                              POOL_STATUS_CLASSES[entry.pool.status] || POOL_STATUS_CLASSES.crawled
                            }`}
                          >
                            {POOL_STATUS_LABELS[entry.pool.status] || entry.pool.status}
                          </span>
                        ) : (
                          <span className="text-xs text-slate-400">未导入</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap">
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => {
                              setShareEntry(entry)
                              setShareResult(null)
                            }}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-blue-50 text-blue-600 hover:bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400"
                          >
                            <Share2 className="w-3.5 h-3.5" />
                            分享
                          </button>
                          {!entry.dir && !entry.pool && (
                            <button
                              onClick={() => setImportEntry(entry)}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-green-50 text-green-600 hover:bg-green-100 dark:bg-green-900/30 dark:text-green-400"
                            >
                              <Rocket className="w-3.5 h-3.5" />
                              上架
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* 分享弹窗 */}
      <ModalShell
        isOpen={shareEntry !== null}
        title={shareEntry?.dir ? '分享文件夹' : '分享文件'}
        onClose={() => setShareEntry(null)}
      >
        {shareEntry && (
          <div className="space-y-4">
            <p className="text-sm text-slate-500 dark:text-slate-400 break-all">{shareEntry.path}</p>
            {shareResult ? (
              <div className="space-y-3">
                <div className="flex items-start gap-2 rounded-lg bg-green-50 dark:bg-green-900/20 p-3">
                  <CheckCircle2 className="w-4 h-4 text-green-600 mt-0.5 flex-shrink-0" />
                  <div className="min-w-0">
                    <a
                      href={shareResult.share_url || '#'}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm text-blue-600 dark:text-blue-400 break-all hover:underline"
                    >
                      {shareResult.share_url}
                    </a>
                    {shareResult.pan_code && (
                      <div className="text-xs text-slate-500 mt-1">提取码：{shareResult.pan_code}</div>
                    )}
                    <div className="text-xs text-slate-400 mt-1">
                      有效期至 {shareResult.share_expire_at ? formatDateTime(new Date(shareResult.share_expire_at).getTime() / 1000) : '-'}
                    </div>
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => handleCopy(shareResult.share_url || '')}
                    className="btn-ios-secondary"
                  >
                    <Copy className="w-4 h-4" />
                    复制链接
                  </button>
                  <button onClick={() => setShareEntry(null)} className="btn-ios-primary">
                    完成
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex justify-end gap-2">
                <button onClick={() => setShareEntry(null)} className="btn-ios-secondary">
                  取消
                </button>
                <button onClick={handleShare} disabled={sharing} className="btn-ios-primary">
                  {sharing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Link2 className="w-4 h-4" />}
                  {sharing ? '创建中...' : '创建分享链接'}
                </button>
              </div>
            )}
          </div>
        )}
      </ModalShell>

      {/* 上架弹窗（导入资料池） */}
      <ImportModal
        entry={importEntry}
        onClose={() => setImportEntry(null)}
        onDone={() => {
          setImportEntry(null)
          loadEntries(currentPath)
        }}
      />
    </div>
  )
}

// ==================== 导入资料池弹窗 ====================

function ImportModal({
  entry,
  onClose,
  onDone,
}: {
  entry: PanEntry | null
  onClose: () => void
  onDone: () => void
}) {
  const { addToast } = useUIStore()
  const [submitting, setSubmitting] = useState(false)
  const [title, setTitle] = useState('')
  const [price, setPrice] = useState('')
  const [description, setDescription] = useState('')
  const [school, setSchool] = useState('')
  const [year, setYear] = useState('')
  const [grade, setGrade] = useState('')
  const [coverImage, setCoverImage] = useState('')
  const [coverPreview, setCoverPreview] = useState('')
  const [uploadingCover, setUploadingCover] = useState(false)

  // 每次打开新文件时重置并预填默认值
  useEffect(() => {
    if (entry) {
      setTitle(defaultTitleFromFileName(entry.file_name))
      setPrice('')
      setDescription('')
      setSchool('')
      setYear('')
      setGrade('')
      setCoverImage('')
      setCoverPreview('')
    }
  }, [entry])

  if (!entry) return null

  const handleCoverUpload = async (file: File) => {
    setUploadingCover(true)
    try {
      const { path, url } = await uploadMaterialPoolCover(file)
      setCoverImage(path)
      setCoverPreview(url)
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '封面上传失败') })
    } finally {
      setUploadingCover(false)
    }
  }

  const handleSubmit = async () => {
    const priceNum = Number(price)
    if (!price || Number.isNaN(priceNum) || priceNum <= 0) {
      addToast({ type: 'error', message: '请填写售价（大于 0）' })
      return
    }
    if (!coverImage) {
      addToast({ type: 'error', message: '请上传封面图（上架必填）' })
      return
    }
    setSubmitting(true)
    try {
      // 1) 导入资料池（幂等：已有记录直接返回）
      const importRes = await importPanFile({
        pan_path: entry.path,
        file_name: entry.file_name,
        file_size: entry.size,
        fid: entry.fid,
      })
      if (!importRes.success || !importRes.data) {
        addToast({ type: 'error', message: importRes.message || '导入资料池失败' })
        return
      }
      const poolId = importRes.data.id
      const existed = importRes.message === '该文件已在资料池中'

      // 2) 补充商品信息（复用资料池编辑接口）
      const updateRes = await updateMaterialPool(poolId, {
        title: title.trim() || undefined,
        price: priceNum,
        description: description.trim() || undefined,
        school: school.trim() || undefined,
        year: year.trim() || undefined,
        grade: grade.trim() || undefined,
        cover_image: coverImage,
      })
      if (!updateRes.success) {
        addToast({ type: 'error', message: updateRes.message || '补充信息保存失败' })
        return
      }

      addToast({
        type: 'success',
        message: existed ? '该文件已在资料池中，已更新补充信息' : '已导入资料池（待审核），请前往资料池审核上架',
      })
      onDone()
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '导入失败') })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalShell isOpen title="补充信息并上架" onClose={onClose} width="max-w-xl">
      <div className="space-y-4">
        <div className="rounded-lg bg-slate-50 dark:bg-slate-900/40 p-3 text-sm">
          <div className="font-medium break-all">{entry.file_name}</div>
          <div className="text-xs text-slate-500 mt-1 break-all">{entry.path}</div>
          <div className="text-xs text-slate-400 mt-0.5">{formatFileSize(entry.size)}</div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="input-group sm:col-span-2">
            <label className="input-label">商品标题（留空则上架时按文件名自动生成）</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} className="input-ios" placeholder="留空自动生成" />
          </div>
          <div className="input-group">
            <label className="input-label">售价（元，必填）</label>
            <input
              type="number"
              min="0"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              className="input-ios"
              placeholder="如：9.90"
            />
          </div>
          <div className="input-group">
            <label className="input-label">年份</label>
            <input type="text" value={year} onChange={(e) => setYear(e.target.value)} className="input-ios" placeholder="如：2026" />
          </div>
          <div className="input-group">
            <label className="input-label">学校</label>
            <input type="text" value={school} onChange={(e) => setSchool(e.target.value)} className="input-ios" placeholder="如：学军中学" />
          </div>
          <div className="input-group">
            <label className="input-label">年级</label>
            <input type="text" value={grade} onChange={(e) => setGrade(e.target.value)} className="input-ios" placeholder="如：高三" />
          </div>
          <div className="input-group sm:col-span-2">
            <label className="input-label">商品描述（可选）</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="input-ios"
              placeholder="留空上架时自动生成"
            />
          </div>
          <div className="input-group sm:col-span-2">
            <label className="input-label">封面图（必填）</label>
            <div className="flex items-center gap-3">
              <label className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 cursor-pointer">
                {uploadingCover ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ImagePlus className="w-3.5 h-3.5" />}
                {uploadingCover ? '上传中...' : '上传封面'}
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  disabled={uploadingCover}
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) handleCoverUpload(file)
                    e.target.value = ''
                  }}
                />
              </label>
              {coverPreview && (
                <img src={coverPreview} alt="封面预览" className="h-12 w-12 rounded-lg object-cover border border-slate-200 dark:border-slate-600" />
              )}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="btn-ios-secondary" disabled={submitting}>
            取消
          </button>
          <button onClick={handleSubmit} disabled={submitting} className="btn-ios-primary">
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
            {submitting ? '导入中...' : '导入资料池'}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}
