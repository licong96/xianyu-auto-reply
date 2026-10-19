/**
 * 资料池页面 - 资料池自动化流水线
 *
 * 功能：
 * 1. 筛选（状态/学校/年份/年级/关键词）+ 分页列表
 * 2. 查看网盘分享链接并复制
 * 3. 编辑资料（标签、售价、标题、描述、封面上传）
 * 4. 审核（通过定价 / 不售）
 * 5. 触发上传夸克网盘
 * 6. 一键上架（选择闲鱼账号：建素材->发布->卡券->续售规则）
 */
import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  BookOpen,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  Loader2,
  MapPin,
  Pencil,
  RefreshCw,
  Rocket,
  Search,
  UploadCloud,
  X,
  XCircle,
} from 'lucide-react'
import { getAccountDetails } from '@/api/accounts'
import {
  approveMaterialPool,
  batchListMaterialPoolGoods,
  coverImageToUrl,
  downloadMaterialPoolFile,
  getMaterialPoolList,
  listMaterialPoolGoods,
  rejectMaterialPool,
  updateMaterialPool,
  uploadMaterialPoolCover,
  uploadMaterialPoolToPan,
  type MaterialPoolCategory,
  type MaterialPoolItem,
  type MaterialPoolPublishOptions,
  SOURCE_TYPE_LABELS,
  SOURCE_TYPE_OPTIONS,
} from '@/api/materialPool'
import {
  getPublishAccountCapability,
  uploadProductImages,
  uploadProductVideos,
  type MaterialVideo,
  type PublishAccountCapability,
  type PublishSpecification,
  type PublishSkuRow,
} from '@/api/productPublish'
import { PlatformCategoryRecommender } from '@/pages/product-publish/PlatformCategoryRecommender'
import { createInternalSkuRows, createInternalSpecifications } from '@/pages/product-publish/MaterialFormModal'
import ProductPublishForm from '@/pages/product-publish/ProductPublishForm'
import ProductVideoUploader from '@/pages/product-publish/ProductVideoUploader'
import AddressPickerModal from '@/pages/product-publish/AddressPickerModal'
import { findDuplicateSpecificationValue, type PublishForm, type ShippingMethod } from '@/pages/product-publish/publishTypes'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { Select } from '@/components/common/Select'
import { PageLoading } from '@/components/common/Loading'
import { ImageUploaderGrid } from '@/components/common/ImageUploaderGrid'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'
import type { AccountDetail } from '@/types'

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

// 多网盘显示名（与子表 pan_type 对应）
const PAN_LABELS: Record<string, string> = {
  quark: '夸克',
  baidu: '百度',
}

const STATUS_LABELS: Record<string, string> = {
  crawled: '已采集',
  uploaded: '已传网盘',
  pending: '待审核',
  approved: '审核通过',
  listing: '上架中',
  listed: '在售',
  rejected: '不售',
  failed: '失败',
}

const STATUS_CLASSES: Record<string, string> = {
  crawled: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300',
  uploaded: 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400',
  pending: 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400',
  approved: 'bg-cyan-50 text-cyan-600 dark:bg-cyan-900/30 dark:text-cyan-400',
  listing: 'bg-indigo-50 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400',
  listed: 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400',
  rejected: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
  failed: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400',
}

function formatDateTime(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatFileSize(size?: number | null) {
  if (!size && size !== 0) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_CLASSES[status] || STATUS_CLASSES.crawled}`}>
      {STATUS_LABELS[status] || status}
    </span>
  )
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

export function MaterialPool() {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(true)
  const [rows, setRows] = useState<MaterialPoolItem[]>([])
  const [accounts, setAccounts] = useState<AccountDetail[]>([])
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceTypeFilter, setSourceTypeFilter] = useState('')
  const [schoolFilter, setSchoolFilter] = useState('')
  const [yearFilter, setYearFilter] = useState('')
  const [gradeFilter, setGradeFilter] = useState('')
  const [keywordFilter, setKeywordFilter] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [submitting, setSubmitting] = useState(false)

  // 弹窗状态
  const [editRow, setEditRow] = useState<MaterialPoolItem | null>(null)
  const [approveRow, setApproveRow] = useState<MaterialPoolItem | null>(null)
  const [listRow, setListRow] = useState<MaterialPoolItem | null>(null)
  const [batchListOpen, setBatchListOpen] = useState(false)
  const [rejectTarget, setRejectTarget] = useState<MaterialPoolItem | null>(null)
  const [uploadTarget, setUploadTarget] = useState<MaterialPoolItem | null>(null)
  const [downloadTarget, setDownloadTarget] = useState<MaterialPoolItem | null>(null)
  const [downloadingId, setDownloadingId] = useState<number | null>(null)

  const accountOptions = useMemo(
    () => [
      { value: '', label: '请选择账号', key: 'placeholder' },
      ...accounts.map((account) => ({
        value: account.id,
        label: account.note ? `${account.id} (${account.note})` : account.id,
        key: account.pk?.toString() || account.id,
      })),
    ],
    [accounts]
  )

  const statusOptions = [
    { value: '', label: '全部状态', key: 'all' },
    ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label, key: value })),
  ]

  const loadRows = async (nextPage: number = page, nextPageSize: number = pageSize) => {
    try {
      setLoading(true)
      const res = await getMaterialPoolList({
        status: statusFilter || undefined,
        source_type: sourceTypeFilter || undefined,
        school: schoolFilter.trim() || undefined,
        year: yearFilter.trim() || undefined,
        grade: gradeFilter.trim() || undefined,
        keyword: keywordFilter.trim() || undefined,
        page: nextPage,
        page_size: nextPageSize,
      })
      if (res.success && res.data) {
        setRows(res.data.list)
        setPage(res.data.page)
        setPageSize(res.data.page_size)
        setTotal(res.data.total)
        setTotalPages(res.data.total_pages)
        setSelectedIds([])
      } else {
        addToast({ type: 'error', message: res.message || '加载资料池失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '加载资料池失败') })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadRows(1, pageSize)
    getAccountDetails()
      .then(setAccounts)
      .catch(() => addToast({ type: 'error', message: '加载账号列表失败' }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSearch = () => loadRows(1, pageSize)

  const handleCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      addToast({ type: 'success', message: '链接已复制' })
    } catch {
      addToast({ type: 'error', message: '复制失败，请手动复制' })
    }
  }

  const handleApprove = async (id: number, payload: { price?: number; title?: string; description?: string }) => {
    setSubmitting(true)
    try {
      const res = await approveMaterialPool(id, payload)
      if (res.success) {
        addToast({ type: 'success', message: '审核通过' })
        setApproveRow(null)
        loadRows()
      } else {
        addToast({ type: 'error', message: res.message || '审核失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '审核失败') })
    } finally {
      setSubmitting(false)
    }
  }

  const handleReject = async () => {
    if (!rejectTarget) return
    setSubmitting(true)
    try {
      const res = await rejectMaterialPool(rejectTarget.id)
      if (res.success) {
        addToast({ type: 'success', message: '已标记不售' })
        setRejectTarget(null)
        loadRows()
      } else {
        addToast({ type: 'error', message: res.message || '操作失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '操作失败') })
    } finally {
      setSubmitting(false)
    }
  }

  const handleUploadPan = async () => {
    if (!uploadTarget) return
    setSubmitting(true)
    try {
      const res = await uploadMaterialPoolToPan(uploadTarget.id)
      if (res.success) {
        addToast({ type: 'success', message: '上传成功，分享链接已生成' })
        setUploadTarget(null)
        loadRows()
      } else {
        addToast({ type: 'error', message: res.message || '上传失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '上传失败') })
    } finally {
      setSubmitting(false)
    }
  }

  const handleDownload = async () => {
    if (!downloadTarget) return
    setSubmitting(true)
    setDownloadingId(downloadTarget.id)
    try {
      const res = await downloadMaterialPoolFile(downloadTarget.id)
      if (res.success) {
        addToast({ type: 'success', message: '下载完成，已上传网盘并生成分享链接' })
        setDownloadTarget(null)
      } else {
        addToast({ type: 'error', message: res.message || '下载失败' })
      }
      loadRows()
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '下载失败') })
      loadRows()
    } finally {
      setSubmitting(false)
      setDownloadingId(null)
    }
  }

  const handleList = async (id: number, accountId: string, category?: MaterialPoolCategory, publishOptions?: MaterialPoolPublishOptions) => {
    setSubmitting(true)
    try {
      const res = await listMaterialPoolGoods(id, accountId, category, publishOptions)
      if (res.success) {
        addToast({ type: 'success', message: '上架完成，商品已在售' })
        setListRow(null)
        loadRows()
        return true
      }
      addToast({ type: 'error', message: res.message || '上架失败' })
      loadRows()
      return false
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '上架失败') })
      loadRows()
      return false
    } finally {
      setSubmitting(false)
    }
  }

  const handleBatchList = async (accountId: string, category?: MaterialPoolCategory, publishOptions?: MaterialPoolPublishOptions) => {
    setSubmitting(true)
    try {
      const res = await batchListMaterialPoolGoods(selectedIds, accountId, category, publishOptions)
      if (res.success && res.data) {
        addToast({
          type: res.data.success_count > 0 ? 'success' : 'error',
          message: res.message || '批量上架完成',
        })
        setBatchListOpen(false)
        loadRows()
        return true
      }
      addToast({ type: 'error', message: res.message || '批量上架失败' })
      return false
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '批量上架失败') })
      return false
    } finally {
      setSubmitting(false)
    }
  }

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const toggleSelectAll = () => {
    if (selectedIds.length === rows.length) {
      setSelectedIds([])
    } else {
      setSelectedIds(rows.map((r) => r.id))
    }
  }

  // 有本地文件 + 未生成分享链接时才允许手动补传网盘（正常流程由「下载试卷」自动完成）
  const canUpload = (row: MaterialPoolItem) =>
    Boolean(row.local_path) && !row.pan_url && ['crawled', 'uploaded', 'failed'].includes(row.status)
  // 无本地文件且未上传网盘：可触发「下载试卷」（接管浏览器下载 + 自动上传网盘）
  const canDownloadFile = (row: MaterialPoolItem) => !row.local_path && !row.pan_url
  const canApprove = (row: MaterialPoolItem) =>
    ['pending', 'crawled', 'uploaded', 'failed', 'rejected'].includes(row.status)
  const canReject = (row: MaterialPoolItem) => !['listed', 'rejected'].includes(row.status)
  const canList = (row: MaterialPoolItem) =>
    Boolean(row.pan_url) && ['approved', 'failed'].includes(row.status)

  if (loading && rows.length === 0) {
    return <PageLoading />
  }

  return (
    <div className="space-y-4">
      <div className="page-header flex-between">
        <div>
          <h1 className="page-title">资料池</h1>
          <p className="page-description">爬虫采集的试卷资料：下载（自动传网盘）、审核、一键上架到闲鱼</p>
        </div>
        <button onClick={() => loadRows()} className="btn-ios-secondary" disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      <div className="vben-card">
        <div className="vben-card-body">
          <div className="flex flex-wrap items-end gap-4">
            <div className="input-group min-w-[160px]">
              <label className="input-label">状态</label>
              <Select value={statusFilter} onChange={setStatusFilter} options={statusOptions} placeholder="全部状态" />
            </div>
            <div className="input-group min-w-[140px]">
              <label className="input-label">来源</label>
              <Select value={sourceTypeFilter} onChange={setSourceTypeFilter} options={[...SOURCE_TYPE_OPTIONS]} placeholder="全部来源" />
            </div>
            <div className="input-group min-w-[140px]">
              <label className="input-label">学校</label>
              <input type="text" value={schoolFilter} onChange={(e) => setSchoolFilter(e.target.value)} placeholder="如：学军中学" className="input-ios" />
            </div>
            <div className="input-group min-w-[100px]">
              <label className="input-label">年份</label>
              <input type="text" value={yearFilter} onChange={(e) => setYearFilter(e.target.value)} placeholder="如：2026" className="input-ios" />
            </div>
            <div className="input-group min-w-[100px]">
              <label className="input-label">年级</label>
              <input type="text" value={gradeFilter} onChange={(e) => setGradeFilter(e.target.value)} placeholder="如：高三" className="input-ios" />
            </div>
            <div className="input-group min-w-[200px]">
              <label className="input-label">关键词</label>
              <input type="text" value={keywordFilter} onChange={(e) => setKeywordFilter(e.target.value)} placeholder="试卷名 / 标题 / 标签" className="input-ios" />
            </div>
            <button onClick={handleSearch} className="btn-ios-primary">
              <Search className="w-4 h-4" />
              查询
            </button>
            {selectedIds.length > 0 && (
              <button onClick={() => setBatchListOpen(true)} className="btn-ios-primary">
                <Rocket className="w-4 h-4" />
                批量上架（{selectedIds.length}）
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="vben-card flex flex-col" style={{ height: 'calc(100vh - 340px)', minHeight: '420px' }}>
        <div className="vben-card-header flex-shrink-0 flex-between">
          <h2 className="vben-card-title">
            <BookOpen className="w-4 h-4 text-blue-500" />
            资料列表
          </h2>
          <span className="badge-primary">{total} 条记录</span>
        </div>
        <div className="table-scroll">
          {loading ? (
            <div className="flex justify-center py-8">
              <RefreshCw className="w-6 h-6 animate-spin text-blue-500" />
            </div>
          ) : (
            <table className="table-ios min-w-[1500px]">
              <thead className="sticky top-0 bg-white dark:bg-slate-800 z-[1]">
                <tr>
                  <th className="w-10">
                    <input type="checkbox" checked={rows.length > 0 && selectedIds.length === rows.length} onChange={toggleSelectAll} />
                  </th>
                  <th>ID</th>
                  <th>试卷</th>
                  <th>学校/年份/年级</th>
                  <th>标签</th>
                  <th>源站数据</th>
                  <th>文件</th>
                  <th>分享链接</th>
                  <th>售价</th>
                  <th>状态</th>
                  <th>商品信息</th>
                  <th>失败原因</th>
                  <th>采集时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={14}>
                      <div className="empty-state py-8">
                        <p className="text-slate-500 dark:text-slate-400">暂无资料，先用爬虫工具采集试卷</p>
                      </div>
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <input type="checkbox" checked={selectedIds.includes(row.id)} onChange={() => toggleSelect(row.id)} />
                      </td>
                      <td className="align-top whitespace-nowrap">{row.id}</td>
                      <td className="align-top min-w-[280px] max-w-[340px]">
                        <div className="flex items-start gap-1.5">
                          <div className="font-medium whitespace-pre-wrap break-words">{row.paper_name}</div>
                          {row.is_dir && (
                            <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400 whitespace-nowrap mt-0.5">
                              文件夹
                            </span>
                          )}
                        </div>
                        {row.source_type && SOURCE_TYPE_LABELS[row.source_type] && (
                          <span className={`inline-block mt-1 px-1.5 py-0.5 rounded text-xs ${
                            row.source_type === 'quark-manual'
                              ? 'bg-violet-50 text-violet-600 dark:bg-violet-900/30 dark:text-violet-400'
                              : 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
                          }`}>
                            {SOURCE_TYPE_LABELS[row.source_type]}
                          </span>
                        )}
                        <a
                          href={row.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 mt-1 text-xs text-blue-600 dark:text-blue-400 break-all hover:underline"
                        >
                          <ExternalLink className="w-3 h-3" />
                          来源
                        </a>
                      </td>
                      <td className="align-top whitespace-nowrap">
                        <div>{row.school || '-'}</div>
                        <div className="text-xs text-slate-500 dark:text-slate-400">
                          {row.year || '-'} / {row.grade || '-'}
                        </div>
                      </td>
                      <td className="align-top min-w-[160px] max-w-[200px]">
                        <div className="flex flex-wrap gap-1">
                          {(row.tags || []).map((tag) => (
                            <span key={tag} className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-xs text-slate-600 dark:text-slate-300">
                              {tag}
                            </span>
                          ))}
                          {(row.tags || []).length === 0 && '-'}
                        </div>
                      </td>
                      <td className="align-top whitespace-nowrap text-xs">
                        <div>浏览 {row.view_count ?? 0}</div>
                        <div>下载 {row.download_count ?? 0}</div>
                        <div className="text-slate-500 dark:text-slate-400">{formatDateTime(row.published_at)}</div>
                      </td>
                      <td className="align-top whitespace-nowrap text-xs">
                        {row.local_path ? formatFileSize(row.file_size) : <span className="text-slate-400">无文件</span>}
                      </td>
                      <td className="align-top min-w-[180px] max-w-[220px]">
                        {row.pan_links && row.pan_links.length > 0 ? (
                          <div className="space-y-1.5">
                            {row.pan_links.filter((l) => l.pan_url).map((l) => (
                              <div key={l.pan_type} className="min-w-0">
                                <div className="flex items-center gap-1">
                                  <span className="flex-shrink-0 px-1 rounded text-[10px] bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-300">
                                    {PAN_LABELS[l.pan_type] || l.pan_type}
                                  </span>
                                  <button
                                    onClick={() => handleCopy(l.pan_code ? `${l.pan_url} 提取码：${l.pan_code}` : l.pan_url!)}
                                    className="p-0.5 text-slate-400 hover:text-blue-500 flex-shrink-0"
                                    title="复制链接"
                                  >
                                    <Copy className="w-3.5 h-3.5" />
                                  </button>
                                </div>
                                <a href={l.pan_url!} target="_blank" rel="noreferrer" className="text-blue-600 dark:text-blue-400 break-all hover:underline text-xs">
                                  {l.pan_url}
                                </a>
                                {l.pan_code && <div className="text-xs text-slate-400 mt-0.5">提取码：{l.pan_code}</div>}
                              </div>
                            ))}
                          </div>
                        ) : row.pan_url ? (
                          <div className="flex items-start gap-1">
                            <a href={row.pan_url} target="_blank" rel="noreferrer" className="text-blue-600 dark:text-blue-400 break-all hover:underline text-xs">
                              {row.pan_url}
                            </a>
                            <button onClick={() => handleCopy(row.pan_url!)} className="p-0.5 text-slate-400 hover:text-blue-500" title="复制链接">
                              <Copy className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ) : (
                          <span className="text-slate-400">未上传</span>
                        )}
                      </td>
                      <td className="align-top whitespace-nowrap">
                        {row.price != null ? `¥${Number(row.price).toFixed(2)}` : '-'}
                      </td>
                      <td className="align-top whitespace-nowrap">
                        <StatusBadge status={row.status} />
                        {row.retry_count > 0 && row.status === 'failed' && (
                          <div className="text-xs text-slate-400 mt-0.5">重试 {row.retry_count} 次</div>
                        )}
                      </td>
                      <td className="align-top min-w-[160px] text-xs">
                        {row.item_id ? (
                          <div>
                            <div className="text-slate-500 dark:text-slate-400">item_id</div>
                            <div className="break-all">{row.item_id}</div>
                          </div>
                        ) : row.account_id ? (
                          <div className="text-slate-500 dark:text-slate-400">账号 {row.account_id}</div>
                        ) : (
                          '-'
                        )}
                      </td>
                      <td className="align-top min-w-[180px] max-w-[240px] text-xs text-red-600 dark:text-red-400 whitespace-pre-wrap break-words">
                        {row.error_message || '-'}
                      </td>
                      <td className="align-top whitespace-nowrap text-xs">{formatDateTime(row.created_at)}</td>
                      <td className="align-top whitespace-nowrap">
                        <div className="flex flex-col gap-1">
                          {canDownloadFile(row) && (
                            <button
                              onClick={() => setDownloadTarget(row)}
                              disabled={downloadingId !== null}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-indigo-50 text-indigo-600 hover:bg-indigo-100 dark:bg-indigo-900/30 dark:text-indigo-400 disabled:opacity-50"
                            >
                              {downloadingId === row.id ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              ) : (
                                <Download className="w-3.5 h-3.5" />
                              )}
                              {downloadingId === row.id ? '下载中...' : '下载试卷'}
                            </button>
                          )}
                          {canUpload(row) && (
                            <button
                              onClick={() => setUploadTarget(row)}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-blue-50 text-blue-600 hover:bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400"
                            >
                              <UploadCloud className="w-3.5 h-3.5" />
                              上传网盘
                            </button>
                          )}
                          {canApprove(row) && (
                            <button
                              onClick={() => setApproveRow(row)}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-cyan-50 text-cyan-600 hover:bg-cyan-100 dark:bg-cyan-900/30 dark:text-cyan-400"
                            >
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              审核通过
                            </button>
                          )}
                          {canList(row) && (
                            <button
                              onClick={() => setListRow(row)}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-green-50 text-green-600 hover:bg-green-100 dark:bg-green-900/30 dark:text-green-400"
                            >
                              <Rocket className="w-3.5 h-3.5" />
                              一键上架
                            </button>
                          )}
                          {canReject(row) && (
                            <button
                              onClick={() => setRejectTarget(row)}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-gray-50 text-gray-500 hover:bg-gray-100 dark:bg-gray-700 dark:text-gray-400"
                            >
                              <XCircle className="w-3.5 h-3.5" />
                              不售
                            </button>
                          )}
                          <button
                            onClick={() => setEditRow(row)}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-slate-50 text-slate-600 hover:bg-slate-100 dark:bg-slate-700 dark:text-slate-300"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                            编辑
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
        </div>

        {total > 0 && (
          <div className="flex-shrink-0 flex flex-col lg:flex-row items-center justify-between px-4 py-3 border-t border-gray-200 dark:border-gray-700 gap-3">
            <div className="flex items-center gap-3 text-sm text-gray-500">
              <span>共 {total} 条记录</span>
              <div className="flex items-center gap-2">
                <span>每页</span>
                <select
                  value={pageSize}
                  onChange={(e) => loadRows(1, Number(e.target.value))}
                  className="px-2 py-1 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-800"
                >
                  {PAGE_SIZE_OPTIONS.map((size) => (
                    <option key={size} value={size}>{size}</option>
                  ))}
                </select>
                <span>条</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-500">第 {page} / {totalPages || 1} 页</span>
              <button onClick={() => loadRows(page - 1, pageSize)} disabled={page <= 1 || loading} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50">
                <ChevronLeft className="w-5 h-5" />
              </button>
              <button onClick={() => loadRows(page + 1, pageSize)} disabled={page >= totalPages || loading} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50">
                <ChevronRight className="w-5 h-5" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 编辑弹窗 */}
      <EditModal
        row={editRow}
        submitting={submitting}
        onClose={() => setEditRow(null)}
        onSubmit={async (id, payload) => {
          setSubmitting(true)
          try {
            const res = await updateMaterialPool(id, payload)
            if (res.success) {
              addToast({ type: 'success', message: '已保存' })
              setEditRow(null)
              loadRows()
            } else {
              addToast({ type: 'error', message: res.message || '保存失败' })
            }
          } catch (error) {
            addToast({ type: 'error', message: getApiErrorMessage(error, '保存失败') })
          } finally {
            setSubmitting(false)
          }
        }}
      />

      {/* 审核弹窗 */}
      <ApproveModal
        row={approveRow}
        submitting={submitting}
        onClose={() => setApproveRow(null)}
        onSubmit={handleApprove}
      />

      {/* 一键上架弹窗 */}
      <ListModal
        row={listRow}
        accounts={accounts}
        accountOptions={accountOptions}
        submitting={submitting}
        onClose={() => setListRow(null)}
        onSubmit={(accountId, category, publishOptions) =>
          listRow ? handleList(listRow.id, accountId, category, publishOptions) : Promise.resolve(false)
        }
      />

      {/* 批量上架弹窗 */}
      <ListModal
        row={null}
        batchCount={batchListOpen ? selectedIds.length : 0}
        accounts={accounts}
        accountOptions={accountOptions}
        submitting={submitting}
        onClose={() => setBatchListOpen(false)}
        onSubmit={(accountId, category, publishOptions) => handleBatchList(accountId, category, publishOptions)}
      />

      {/* 不售确认 */}
      <ConfirmModal
        isOpen={rejectTarget !== null}
        title="标记不售"
        message={`确定将「${rejectTarget?.paper_name || ''}」标记为不售吗？之后可以重新审核。`}
        confirmText="确定"
        type="warning"
        loading={submitting}
        onConfirm={handleReject}
        onCancel={() => setRejectTarget(null)}
      />

      {/* 上传网盘确认 */}
      <ConfirmModal
        isOpen={uploadTarget !== null}
        title="上传到夸克网盘"
        message={`确定将「${uploadTarget?.paper_name || ''}」上传到夸克网盘并创建分享链接吗？大文件需要一些时间。`}
        confirmText="开始上传"
        type="info"
        loading={submitting}
        onConfirm={handleUploadPan}
        onCancel={() => setUploadTarget(null)}
      />

      {/* 下载试卷确认 */}
      <ConfirmModal
        isOpen={downloadTarget !== null}
        title="下载试卷"
        message={`将接管本地浏览器打开详情页下载「${downloadTarget?.paper_name || ''}」，成功后自动上传夸克网盘并生成分享链接。请确保爬虫浏览器（端口 9333）已登录学科网会员；全程约 1-5 分钟，期间请勿重复点击。`}
        confirmText="开始下载"
        type="info"
        loading={submitting}
        onConfirm={handleDownload}
        onCancel={() => setDownloadTarget(null)}
      />
    </div>
  )
}

// ==================== 编辑弹窗 ====================

function EditModal({
  row,
  submitting,
  onClose,
  onSubmit,
}: {
  row: MaterialPoolItem | null
  submitting: boolean
  onClose: () => void
  onSubmit: (id: number, payload: Record<string, unknown>) => void
}) {
  const { addToast } = useUIStore()
  const [form, setForm] = useState({ paper_name: '', school: '', year: '', grade: '', tags: '', title: '', description: '', price: '' })
  const [coverImage, setCoverImage] = useState('')
  const [uploading, setUploading] = useState(false)

  useEffect(() => {
    if (row) {
      setForm({
        paper_name: row.paper_name || '',
        school: row.school || '',
        year: row.year || '',
        grade: row.grade || '',
        tags: (row.tags || []).join('，'),
        title: row.title || '',
        description: row.description || '',
        price: row.price != null ? String(row.price) : '',
      })
      setCoverImage(row.cover_image || '')
    }
  }, [row])

  const handleUploadCover = async (file: File | undefined) => {
    if (!file) return
    setUploading(true)
    try {
      const { path } = await uploadMaterialPoolCover(file)
      setCoverImage(path)
      addToast({ type: 'success', message: '封面上传成功' })
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '封面上传失败') })
    } finally {
      setUploading(false)
    }
  }

  const handleSubmit = () => {
    if (!row) return
    const payload: Record<string, unknown> = {
      paper_name: form.paper_name.trim(),
      school: form.school.trim() || null,
      year: form.year.trim() || null,
      grade: form.grade.trim() || null,
      tags: form.tags.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean),
      title: form.title.trim() || null,
      description: form.description.trim() || null,
      cover_image: coverImage || null,
    }
    if (form.price.trim()) {
      const price = Number(form.price)
      if (Number.isNaN(price) || price < 0) {
        addToast({ type: 'error', message: '售价格式不正确' })
        return
      }
      payload.price = price
    }
    onSubmit(row.id, payload)
  }

  return (
    <ModalShell isOpen={row !== null} title="编辑资料" onClose={onClose} width="max-w-2xl">
      <div className="space-y-4">
        <div>
          <label className="input-label">试卷名字</label>
          <textarea value={form.paper_name} onChange={(e) => setForm({ ...form, paper_name: e.target.value })} rows={2} className="input-ios w-full" />
        </div>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="input-label">学校</label>
            <input type="text" value={form.school} onChange={(e) => setForm({ ...form, school: e.target.value })} className="input-ios w-full" />
          </div>
          <div>
            <label className="input-label">年份</label>
            <input type="text" value={form.year} onChange={(e) => setForm({ ...form, year: e.target.value })} className="input-ios w-full" />
          </div>
          <div>
            <label className="input-label">年级</label>
            <input type="text" value={form.grade} onChange={(e) => setForm({ ...form, grade: e.target.value })} className="input-ios w-full" />
          </div>
        </div>
        <div>
          <label className="input-label">标签（逗号分隔）</label>
          <input type="text" value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="如：数学, 期中, 含答案" className="input-ios w-full" />
        </div>
        <div>
          <label className="input-label">商品标题（留空自动生成）</label>
          <input type="text" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="input-ios w-full" />
        </div>
        <div>
          <label className="input-label">商品描述</label>
          <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={4} className="input-ios w-full" placeholder="留空自动生成" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="input-label">售价（元）</label>
            <input type="number" min="0" step="0.01" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} className="input-ios w-full" />
          </div>
          <div>
            <label className="input-label">封面图（可选）</label>
            <div className="flex items-center gap-2">
              <label className="btn-ios-secondary cursor-pointer inline-flex items-center gap-1">
                {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <UploadCloud className="w-4 h-4" />}
                {uploading ? '上传中...' : coverImage ? '重新上传' : '上传封面'}
                <input type="file" accept="image/*" className="hidden" onChange={(e) => handleUploadCover(e.target.files?.[0])} />
              </label>
              {!coverImage && (
                <span className="text-xs text-slate-400">不上传也能上架，直接在一键上架弹窗传商品图片即可</span>
              )}
            </div>
          </div>
        </div>
        {coverImage && (
          <div className="flex items-center gap-3">
            <img src={coverImageToUrl(coverImage)} alt="封面预览" className="w-24 h-24 object-cover rounded-lg border border-slate-200 dark:border-slate-700" />
            <button onClick={() => setCoverImage('')} className="text-xs text-red-500 hover:underline">移除封面</button>
          </div>
        )}
        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="btn-ios-secondary">取消</button>
          <button onClick={handleSubmit} disabled={submitting} className="btn-ios-primary inline-flex items-center gap-2">
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            保存
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

// ==================== 审核弹窗 ====================

function ApproveModal({
  row,
  submitting,
  onClose,
  onSubmit,
}: {
  row: MaterialPoolItem | null
  submitting: boolean
  onClose: () => void
  onSubmit: (id: number, payload: { price?: number; title?: string; description?: string }) => void
}) {
  const [price, setPrice] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (row) {
      setPrice(row.price != null ? String(row.price) : '')
      setTitle(row.title || '')
      setDescription(row.description || '')
    }
  }, [row])

  const handleSubmit = () => {
    if (!row) return
    const payload: { price?: number; title?: string; description?: string } = {}
    if (price.trim()) payload.price = Number(price)
    if (title.trim()) payload.title = title.trim()
    if (description.trim()) payload.description = description.trim()
    onSubmit(row.id, payload)
  }

  return (
    <ModalShell isOpen={row !== null} title="审核通过" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-slate-600 dark:text-slate-400 break-all">
          「{row?.paper_name}」
        </p>
        <div>
          <label className="input-label">售价（元，上架必填）</label>
          <input type="number" min="0" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} className="input-ios w-full" placeholder="如：1.99" />
        </div>
        <div>
          <label className="input-label">商品标题（留空自动生成）</label>
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} className="input-ios w-full" />
        </div>
        <div>
          <label className="input-label">商品描述（留空自动生成）</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className="input-ios w-full" />
        </div>
        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="btn-ios-secondary">取消</button>
          <button onClick={handleSubmit} disabled={submitting} className="btn-ios-primary inline-flex items-center gap-2">
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            审核通过
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

// ==================== 一键上架弹窗 ====================

/** 与后端 _build_goods_title 生成规则保持一致：剥「精品解析：」等前缀，学校+年份+年级+试卷名（截 30 字） */
function buildGoodsTitle(row: MaterialPoolItem): string {
  let name = row.paper_name || '试卷资料'
  for (const prefix of ['精品解析：', '精品解析:', '解析：', '解析:']) {
    if (name.startsWith(prefix)) {
      name = name.slice(prefix.length)
      break
    }
  }
  const base = [row.school, row.year, row.grade].filter(Boolean).join('')
  const title = base ? `${base}${name}` : name
  return title.slice(0, 30)
}

/** 与后端 _build_goods_description 生成规则保持一致（截 1500 字） */
function buildGoodsDescription(row: MaterialPoolItem): string {
  const lines = [`【资料名称】${row.paper_name || '-'}`]
  if (row.school) lines.push(`【学校】${row.school}`)
  if (row.year) lines.push(`【年份】${row.year}`)
  if (row.grade) lines.push(`【年级】${row.grade}`)
  if (row.tags?.length) lines.push(`【标签】${row.tags.join(' ')}`)
  lines.push('', '付款后自动发送网盘下载链接，请尽快保存。')
  return lines.join('\n').slice(0, 1500)
}

function buildEmptyCategoryForm(accountId: string, title: string, description: string): PublishForm {
  return {
    account_id: accountId,
    title,
    description,
    price: '',
    original_price: '',
    category: '',
    platform_category_id: '',
    platform_category_name: '',
    platform_channel_category_id: '',
    platform_channel_category_name: '',
    platform_leaf_id: '',
    platform_tb_category_id: '',
    platform_category_path: [],
    platform_attributes: [],
    category_source: 'manual',
    videos: [],
    quantity: 1,
    address: '',
    delivery_method: 'express',
    shipping_method: 'free',
    support_pickup: false,
    postage: '0',
    brand: '',
    condition: '全新',
    specifications: [],
    sku_rows: [],
  }
}

// 成色选项，与素材库 AI 上架面板保持一致
const CONDITION_OPTIONS = ['全新', '几乎全新', '轻微使用痕迹', '明显使用痕迹', '重度使用痕迹']

// 运费方式选项，与商品发布表单保持一致
const SHIPPING_OPTIONS: Array<{ value: ShippingMethod; label: string }> = [
  { value: 'free', label: '包邮' },
  { value: 'distance', label: '按距离计费' },
  { value: 'fixed', label: '一口价' },
  { value: 'template', label: '运费模板' },
  { value: 'none', label: '无需邮寄' },
]

function ListModal({
  row,
  batchCount = 0,
  accounts,
  accountOptions,
  submitting,
  onClose,
  onSubmit,
}: {
  /** 单个上架的行；批量模式传 null */
  row: MaterialPoolItem | null
  /** 批量模式：选中行数（>0 即批量） */
  batchCount?: number
  accounts: AccountDetail[]
  accountOptions: Array<{ value: string; label: string; key: string }>
  submitting: boolean
  onClose: () => void
  onSubmit: (accountId: string, category?: MaterialPoolCategory, publishOptions?: MaterialPoolPublishOptions) => Promise<boolean>
}) {
  const { addToast } = useUIStore()
  const isBatch = batchCount > 0
  const isOpen = isBatch || row !== null
  const [accountId, setAccountId] = useState('')
  // 批量模式：类目推荐用的表单（标题/描述仅用于推荐）
  const [catForm, setCatForm] = useState<PublishForm>(() => buildEmptyCategoryForm('', '', ''))
  // 单个模式：完整发布表单（与素材库共用 ProductPublishForm）
  const [form, setForm] = useState<PublishForm>(() => buildEmptyCategoryForm('', '', ''))
  const [categoryLocked, setCategoryLocked] = useState(false)
  const [images, setImages] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const [capability, setCapability] = useState<PublishAccountCapability | null>(null)
  const [capabilityLoading, setCapabilityLoading] = useState(false)
  // 批量模式：公共发布选项
  const [sharedForm, setSharedForm] = useState({
    shipping_method: 'free' as ShippingMethod,
    postage: '0',
    support_pickup: false,
    address: '',
    address_expected_text: undefined as string | undefined,
    quantity: 1,
    condition: '全新',
    brand: '',
  })
  const [showAddressPicker, setShowAddressPicker] = useState(false)

  // 单个模式：打开弹窗时预填——标题/描述/价格（行值或自动生成）、图片（已存选项或封面）、
  // 类目（行内已持久化）、其余发布选项（行 publish_options）
  useEffect(() => {
    if (isBatch || !row) return
    const opts = row.publish_options
    const specifications = createInternalSpecifications((opts?.specifications ?? []) as unknown as PublishSpecification[])
    const title = row.title?.trim() || buildGoodsTitle(row)
    const description = row.description?.trim() || buildGoodsDescription(row)
    setImages(opts?.images?.length ? [...opts.images] : row.cover_image ? [row.cover_image] : [])
    setCategoryLocked(Boolean(row.platform_category_id || row.platform_channel_category_id))
    setForm({
      ...buildEmptyCategoryForm('', title, description),
      price: opts?.price != null ? String(opts.price) : row.price != null ? String(row.price) : '',
      original_price: opts?.original_price != null ? String(opts.original_price) : '',
      ...(row.platform_category_id || row.platform_channel_category_id
        ? {
            category: row.platform_channel_category_name || row.platform_category_name || '',
            platform_category_id: row.platform_category_id || '',
            platform_category_name: row.platform_category_name || '',
            platform_channel_category_id: row.platform_channel_category_id || '',
            platform_channel_category_name: row.platform_channel_category_name || '',
            platform_category_path: (row.platform_category_path || []) as PublishForm['platform_category_path'],
            platform_attributes: (row.platform_attributes || []) as PublishForm['platform_attributes'],
            category_source: 'recommendation' as const,
          }
        : {}),
      videos: (opts?.videos ?? []) as MaterialVideo[],
      quantity: opts?.quantity ?? 1,
      address: opts?.address ?? '',
      address_expected_text: opts?.address_expected_text,
      delivery_method: opts?.delivery_method ?? 'express',
      shipping_method: opts?.shipping_method ?? 'free',
      support_pickup: opts?.support_pickup ?? false,
      postage: String(opts?.postage ?? 0),
      brand: opts?.brand ?? '',
      condition: opts?.condition ?? '全新',
      specifications,
      sku_rows: createInternalSkuRows((opts?.sku_rows ?? []) as unknown as PublishSkuRow[], specifications),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row, isBatch])

  // 批量模式：打开弹窗时重置公共选项，标题/描述仅用于类目推荐
  useEffect(() => {
    if (!isBatch) return
    setCatForm(buildEmptyCategoryForm('', '', '试卷资料 电子版'))
    setSharedForm({
      shipping_method: 'free',
      postage: '0',
      support_pickup: false,
      address: '',
      address_expected_text: undefined,
      quantity: 1,
      condition: '全新',
      brand: '',
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchCount])

  useEffect(() => {
    if (accounts.length === 1 && !accountId) setAccountId(accounts[0].id)
  }, [accounts, accountId])

  // 账号变化时同步给类目推荐组件，使其按该账号重新推荐
  useEffect(() => {
    setCatForm((current) => (current.account_id === accountId ? current : { ...current, account_id: accountId }))
    setForm((current) => (current.account_id === accountId ? current : { ...current, account_id: accountId }))
  }, [accountId])

  // 单个模式：检测账号发布能力（普通卖家收起鱼小铺字段：规格/库存/视频）
  useEffect(() => {
    if (isBatch || !accountId) {
      setCapability(null)
      return
    }
    let cancelled = false
    setCapability(null)
    setCapabilityLoading(true)
    getPublishAccountCapability(accountId)
      .then((response) => {
        if (!cancelled) setCapability(response.data ?? null)
      })
      .catch(() => {
        if (!cancelled) setCapability(null)
      })
      .finally(() => {
        if (!cancelled) setCapabilityLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [accountId, isBatch])

  const isFishShop = capability?.is_fish_shop !== false
  const hasCategory = isBatch
    ? Boolean(catForm.platform_category_id || catForm.platform_channel_category_id)
    : Boolean(form.platform_category_id || form.platform_channel_category_id)
  const activeCategoryForm = isBatch ? catForm : form
  const setActiveCategoryForm = isBatch
    ? (patch: Partial<PublishForm>) => setCatForm((current) => ({ ...current, ...patch }))
    : (patch: Partial<PublishForm>) => setForm((current) => ({ ...current, ...patch }))

  const handleUploadImages = async (files: File[]) => {
    if (!files.length) return
    if (images.length + files.length > 9) {
      addToast({ type: 'warning', message: '最多上传9张图片' })
      return
    }
    setUploading(true)
    try {
      const response = await uploadProductImages(files)
      if (!response.success || !response.data) {
        addToast({ type: 'error', message: response.message || '图片上传失败' })
        return
      }
      const paths = response.data.paths?.length ? response.data.paths : response.data.urls
      setImages((current) => [...current, ...paths])
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '图片上传失败') })
    } finally {
      setUploading(false)
    }
  }

  const handleSpecUpload = async (file: File): Promise<string | null> => {
    setUploading(true)
    try {
      const response = await uploadProductImages([file])
      if (!response.success || !response.data) {
        addToast({ type: 'error', message: response.message || '规格图片上传失败' })
        return null
      }
      return response.data.paths[0] || response.data.urls[0] || null
    } catch {
      addToast({ type: 'error', message: '规格图片上传失败' })
      return null
    } finally {
      setUploading(false)
    }
  }

  const handleVideoUpload = async (file: File): Promise<MaterialVideo | null> => {
    setUploading(true)
    try {
      const response = await uploadProductVideos([file])
      if (!response.success || !response.data) {
        addToast({ type: 'error', message: response.message || '视频上传失败' })
        return null
      }
      return response.data.videos[0] || { url: response.data.urls[0], path: response.data.paths[0], name: file.name, size: file.size }
    } catch {
      addToast({ type: 'error', message: '视频上传失败' })
      return null
    } finally {
      setUploading(false)
    }
  }

  const buildCategory = (): MaterialPoolCategory => ({
    category: activeCategoryForm.category,
    platform_category_id: activeCategoryForm.platform_category_id,
    platform_category_name: activeCategoryForm.platform_category_name,
    platform_channel_category_id: activeCategoryForm.platform_channel_category_id,
    platform_channel_category_name: activeCategoryForm.platform_channel_category_name,
    platform_category_path: activeCategoryForm.platform_category_path,
    platform_attributes: activeCategoryForm.platform_attributes as unknown as Array<Record<string, unknown>>,
    category_source: activeCategoryForm.category_source,
  })

  const handleSubmit = async () => {
    if (!accountId) return
    if (!hasCategory) {
      addToast({ type: 'error', message: '请先选择商品类目（下方推荐列表点选其一）' })
      return
    }
    const category = buildCategory()

    if (isBatch) {
      const publishOptions: MaterialPoolPublishOptions = {
        quantity: Math.max(1, Number(sharedForm.quantity) || 1),
        delivery_method: sharedForm.shipping_method === 'none' ? 'pickup' : 'express',
        shipping_method: sharedForm.shipping_method,
        support_pickup: sharedForm.support_pickup,
        postage: Number(sharedForm.postage) || 0,
        address: sharedForm.address.trim() || undefined,
        address_expected_text: sharedForm.address_expected_text?.trim() || undefined,
        brand: sharedForm.brand.trim() || undefined,
        condition: sharedForm.condition,
      }
      await onSubmit(accountId, category, publishOptions)
      return
    }

    if (!row) return
    if (!form.title.trim()) {
      addToast({ type: 'warning', message: '请填写商品标题' })
      return
    }
    if (!form.description.trim()) {
      addToast({ type: 'warning', message: '请填写商品描述' })
      return
    }
    if (form.description.length > 1500) {
      addToast({ type: 'warning', message: '商品描述不能超过1500字' })
      return
    }
    const price = Number(form.price)
    if (!form.price || Number.isNaN(price) || price <= 0) {
      addToast({ type: 'warning', message: '请填写有效价格' })
      return
    }
    if (images.length === 0) {
      addToast({ type: 'warning', message: '请至少保留一张商品图片' })
      return
    }
    if (isFishShop && form.specifications.length > 0) {
      const invalidSpec = form.specifications.find((spec) => !spec.name.trim() || !spec.values.some((value) => value.name.trim()))
      if (invalidSpec) {
        addToast({ type: 'warning', message: '请完善商品规格类型和规格值' })
        return
      }
      const duplicateSpecValue = findDuplicateSpecificationValue(form.specifications)
      if (duplicateSpecValue) {
        addToast({ type: 'warning', message: `规格“${duplicateSpecValue.specificationName}”存在重复规格值：${duplicateSpecValue.valueName}` })
        return
      }
      if (!form.sku_rows.length) {
        addToast({ type: 'warning', message: '请等待规格组合生成后再上架' })
        return
      }
      const invalidSku = form.sku_rows.find((skuRow) => !skuRow.price || Number(skuRow.price) <= 0 || !skuRow.stock.trim() || Number(skuRow.stock) < 0)
      if (invalidSku) {
        addToast({ type: 'warning', message: '请完善所有规格的价格和库存' })
        return
      }
    }

    // 普通卖家不发视频/规格/库存（与单品发布行为一致）
    const publishOptions: MaterialPoolPublishOptions = {
      title: form.title.trim(),
      description: form.description,
      price,
      original_price: form.original_price.trim() ? Number(form.original_price) : undefined,
      images,
      videos: isFishShop ? form.videos : [],
      quantity: isFishShop ? form.quantity : 1,
      delivery_method: form.delivery_method,
      shipping_method: form.shipping_method,
      support_pickup: form.support_pickup,
      postage: Number(form.postage) || 0,
      address: form.address.trim() || undefined,
      address_expected_text: form.address_expected_text?.trim() || undefined,
      brand: form.brand.trim() || undefined,
      condition: form.condition,
      specifications: isFishShop
        ? form.specifications
            .filter((spec) => spec.name.trim())
            .map((spec) => ({
              name: spec.name.trim(),
              support_image: spec.supportImage,
              values: spec.values
                .filter((value) => value.name.trim())
                .map((value) => ({ name: value.name.trim(), image: value.image || undefined })),
            }))
        : [],
      sku_rows: isFishShop
        ? form.sku_rows.map((skuRow) => ({ specs: skuRow.specs, price: Number(skuRow.price), stock: Number(skuRow.stock) || 0 }))
        : [],
    }
    await onSubmit(accountId, category, publishOptions)
  }

  const categoryBlock = (
    <div>
      <label className="input-label">商品类目（根据标题自动推荐，点选确认）</label>
      <div className="mt-1 rounded-xl border border-slate-200 dark:border-slate-700 p-3">
        <PlatformCategoryRecommender
          form={activeCategoryForm}
          onChange={setActiveCategoryForm}
          categoryLocked={!isBatch && categoryLocked}
          onReselectCategory={() => setCategoryLocked(false)}
        />
      </div>
    </div>
  )

  if (isBatch) {
    return (
      <ModalShell isOpen={isOpen} title={`批量上架 ${batchCount} 条资料`} onClose={onClose} width="max-w-2xl">
        <div className="space-y-4">
          <div className="rounded-lg bg-slate-50 dark:bg-slate-700/50 p-3 text-xs text-slate-500 dark:text-slate-400 space-y-1">
            <p>上架流程：创建商品素材 → 发布到闲鱼 → 创建网盘卡券并绑定 → 开启自动续售。</p>
            <p>类目与公共选项会应用到本批每一条，并与各条已保存的发布选项合并；失败重试无需重新选择。</p>
          </div>
          <div>
            <label className="input-label">选择闲鱼账号</label>
            <Select value={accountId} onChange={setAccountId} options={accountOptions} placeholder="请选择账号" />
          </div>
          <div className="vben-card">
            <div className="vben-card-header"><h2 className="vben-card-title">公共发布选项</h2><span className="text-xs text-slate-400">标题/图片等各条用自己的，此处为共享默认值</span></div>
            <div className="vben-card-body space-y-4">
              <div>
                <label className="input-label">发货设置</label>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  {SHIPPING_OPTIONS.map((option) => (
                    <label key={option.value} className="inline-flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300 cursor-pointer">
                      <input
                        type="radio"
                        name="pool_batch_shipping"
                        checked={sharedForm.shipping_method === option.value}
                        onChange={() => setSharedForm((current) => ({ ...current, shipping_method: option.value }))}
                      />
                      {option.label}
                    </label>
                  ))}
                  <label className="inline-flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300 cursor-pointer">
                    <span>支持自提</span>
                    <input
                      type="checkbox"
                      checked={sharedForm.support_pickup}
                      onChange={(event) => setSharedForm((current) => ({ ...current, support_pickup: event.target.checked }))}
                    />
                  </label>
                </div>
                {sharedForm.shipping_method !== 'free' && sharedForm.shipping_method !== 'none' && (
                  <div className="mt-3 max-w-[200px]">
                    <label className="input-label">运费（元）</label>
                    <input
                      type="number"
                      className="input-ios"
                      min="0"
                      step="0.01"
                      placeholder="0.00"
                      value={sharedForm.postage}
                      onChange={(event) => setSharedForm((current) => ({ ...current, postage: event.target.value }))}
                    />
                  </div>
                )}
                <div className="mt-3">
                  <label className="input-label">宝贝所在地（各条单独设置过的不会被覆盖）</label>
                  <button type="button" className="input-ios text-left flex items-center gap-2" onClick={() => setShowAddressPicker(true)}>
                    <MapPin className="w-4 h-4 text-slate-500" />
                    <span className="truncate flex-1">{sharedForm.address || '不设置（跟随各条默认）'}</span>
                    <ChevronRight className="w-4 h-4 text-slate-400" />
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="input-group">
                  <label className="input-label">成色</label>
                  <select className="input-ios" value={sharedForm.condition} onChange={(event) => setSharedForm((current) => ({ ...current, condition: event.target.value }))}>
                    {CONDITION_OPTIONS.map((item) => <option key={item} value={item}>{item}</option>)}
                  </select>
                </div>
                <div className="input-group">
                  <label className="input-label">品牌</label>
                  <input className="input-ios" placeholder="可空" value={sharedForm.brand} onChange={(event) => setSharedForm((current) => ({ ...current, brand: event.target.value }))} />
                </div>
                <div className="input-group">
                  <label className="input-label">发布数量</label>
                  <input
                    type="number"
                    min="1"
                    className="input-ios"
                    value={sharedForm.quantity}
                    onChange={(event) => setSharedForm((current) => ({ ...current, quantity: Math.max(1, Number(event.target.value) || 1) }))}
                  />
                </div>
              </div>
            </div>
          </div>
          {categoryBlock}
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={onClose} className="btn-ios-secondary">取消</button>
            <button
              onClick={handleSubmit}
              disabled={submitting || !accountId}
              className="btn-ios-primary inline-flex items-center gap-2"
            >
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              确认上架
            </button>
          </div>
          <AddressPickerModal
            open={showAddressPicker}
            currentValue={sharedForm.address}
            onClose={() => setShowAddressPicker(false)}
            onSelect={(address, expectedText) => {
              setSharedForm((current) => ({ ...current, address, address_expected_text: expectedText }))
              setShowAddressPicker(false)
            }}
          />
        </div>
      </ModalShell>
    )
  }

  return (
    <ModalShell isOpen={isOpen} title="一键上架到闲鱼" onClose={onClose} width="max-w-4xl">
      <div className="space-y-4">
        <p className="text-sm text-slate-600 dark:text-slate-400 break-all">
          「{row?.paper_name}」
        </p>
        <div className="rounded-lg bg-slate-50 dark:bg-slate-700/50 p-3 text-xs text-slate-500 dark:text-slate-400 space-y-1">
          <p>上架流程：创建商品素材 → 发布到闲鱼 → 创建网盘卡券并绑定 → 开启自动续售。</p>
          <p>以下选项与素材库字段一致，编辑后会同步进素材库；类目与选项选择后会被记住，失败重试无需重新选择。</p>
        </div>
        <div>
          <label className="input-label">选择闲鱼账号</label>
          <Select value={accountId} onChange={setAccountId} options={accountOptions} placeholder="请选择账号" />
        </div>

        <div className="vben-card">
          <div className="vben-card-header">
            <h2 className="vben-card-title">商品图片与视频</h2>
            <span className="text-xs text-slate-400">{images.length}/9 图 · {form.videos.length}/3 视频</span>
          </div>
          <div className="vben-card-body">
            <ImageUploaderGrid
              images={images}
              toUrl={coverImageToUrl}
              onChange={setImages}
              onUploadFiles={handleUploadImages}
              uploading={uploading}
              maxCount={9}
            />
            <ProductVideoUploader
              videos={form.videos}
              onUploadVideo={handleVideoUpload}
              onChange={(videos) => setForm((current) => ({ ...current, videos }))}
            />
          </div>
        </div>

        <ProductPublishForm
          form={form}
          setForm={setForm}
          accounts={[]}
          showAccount={false}
          onUploadSpecImage={handleSpecUpload}
          categoryLocked={categoryLocked}
          onCategoryEdit={() => setCategoryLocked(false)}
          unlockCategoryOnTextChange={false}
          accountCapability={capability}
          capabilityLoading={capabilityLoading}
        />

        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="btn-ios-secondary">取消</button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !accountId || uploading}
            className="btn-ios-primary inline-flex items-center gap-2"
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            确认上架
          </button>
        </div>
      </div>
    </ModalShell>
  )
}
