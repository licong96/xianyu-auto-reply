/**
 * 资料池 API 接口层 - 资料池自动化流水线
 *
 * 功能：
 * 1. 资料池分页列表 / 详情 / 编辑
 * 2. 审核（通过 / 不售）
 * 3. 触发上传夸克网盘
 * 4. 一键上架 / 批量上架
 */
import { get, post, put } from '@/utils/request'
import { toPreviewUrl } from '@/utils/staticUrl'
import type { ApiResponse } from '@/types'
import type { MaterialVideo } from '@/api/productPublish'

const PREFIX = '/api/v1/material-pool'

// ==================== 类型定义 ====================

export interface MaterialPoolPanLink {
  pan_type: string
  pan_path: string
  pan_url: string | null
  pan_code: string | null
  share_id: string | null
  share_expire_at: string | null
}

export interface MaterialPoolItem {
  id: number
  paper_name: string
  school: string | null
  year: string | null
  grade: string | null
  tags: string[]
  published_at: string | null
  view_count: number
  download_count: number
  source_url: string
  source_type: string
  local_path: string | null
  file_size: number | null
  pan_url: string | null
  pan_code: string | null
  /** 多网盘链接子表（空数组时回退 pan_url 单链接展示） */
  pan_links: MaterialPoolPanLink[]
  share_expire_at: string | null
  title: string | null
  description: string | null
  price: number | null
  cover_image: string | null
  platform_category_id: string | null
  platform_category_name: string | null
  platform_channel_category_id: string | null
  platform_channel_category_name: string | null
  platform_category_path: Array<{ id: string; name: string }> | null
  platform_attributes: Array<Record<string, unknown>> | null
  /** 发布选项覆盖项（一键上架弹窗编辑，空则走默认行为） */
  publish_options: MaterialPoolPublishOptions | null
  /** 网盘直选导入的是整个文件夹（分享链接为文件夹链接） */
  is_dir: boolean
  status: string
  account_id: string | null
  material_id: number | null
  item_id: string | null
  card_id: number | null
  relist_rule_id: number | null
  error_message: string | null
  retry_count: number
  owner_id: number
  created_at: string | null
  updated_at: string | null
}

export interface MaterialPoolListResult {
  list: MaterialPoolItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface MaterialPoolListParams {
  status?: string
  school?: string
  year?: string
  grade?: string
  keyword?: string
  /** 来源筛选：zxxk=学科网爬虫，quark-manual=网盘直选 */
  source_type?: string
  page?: number
  page_size?: number
}

/** 来源选项（与后端 source_type 对应） */
export const SOURCE_TYPE_OPTIONS = [
  { value: '', label: '全部来源' },
  { value: 'zxxk', label: '学科网爬虫' },
  { value: 'quark-manual', label: '网盘直选' },
] as const

/** 来源徽章样式 */
export const SOURCE_TYPE_LABELS: Record<string, string> = {
  zxxk: '学科网',
  'quark-manual': '网盘直选',
}

export interface MaterialPoolUpdatePayload {
  paper_name?: string
  school?: string
  year?: string
  grade?: string
  tags?: string[]
  title?: string
  description?: string
  price?: number
  cover_image?: string
}

/** 一键上架选择的类目（与商品发布页类目推荐组件字段一致） */
export interface MaterialPoolCategory {
  category?: string
  platform_category_id?: string
  platform_category_name?: string
  platform_channel_category_id?: string
  platform_channel_category_name?: string
  platform_category_path?: Array<{ id: string; name: string }>
  platform_attributes?: Array<Record<string, unknown>>
  category_source?: 'manual' | 'recommendation'
}

/** 一键上架发布选项（与素材库/单品发布字段对齐，全部可选） */
export interface MaterialPoolPublishOptions {
  title?: string
  description?: string
  price?: number
  original_price?: number
  images?: string[]
  videos?: MaterialVideo[]
  quantity?: number
  specifications?: Array<Record<string, unknown>>
  sku_rows?: Array<Record<string, unknown>>
  address?: string
  address_expected_text?: string
  delivery_method?: 'express' | 'pickup'
  shipping_method?: 'free' | 'distance' | 'fixed' | 'template' | 'none'
  support_pickup?: boolean
  postage?: number
  brand?: string
  condition?: string
}

// ==================== 接口请求 ====================

export const getMaterialPoolList = async (params: MaterialPoolListParams): Promise<ApiResponse<MaterialPoolListResult>> => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.append(key, String(value))
    }
  })
  return get<ApiResponse<MaterialPoolListResult>>(`${PREFIX}?${query.toString()}`)
}

export const getMaterialPoolDetail = async (id: number): Promise<ApiResponse<MaterialPoolItem>> => {
  return get<ApiResponse<MaterialPoolItem>>(`${PREFIX}/${id}`)
}

export const updateMaterialPool = async (id: number, payload: MaterialPoolUpdatePayload): Promise<ApiResponse<MaterialPoolItem>> => {
  return put<ApiResponse<MaterialPoolItem>>(`${PREFIX}/${id}`, payload)
}

export const approveMaterialPool = async (id: number, payload: { price?: number; title?: string; description?: string }): Promise<ApiResponse<MaterialPoolItem>> => {
  return post<ApiResponse<MaterialPoolItem>>(`${PREFIX}/${id}/approve`, payload)
}

export const rejectMaterialPool = async (id: number): Promise<ApiResponse<MaterialPoolItem>> => {
  return post<ApiResponse<MaterialPoolItem>>(`${PREFIX}/${id}/reject`, {})
}

export const uploadMaterialPoolToPan = async (id: number): Promise<ApiResponse<{ id: number; pan_url: string; pan_code: string | null; share_id: string | null }>> => {
  return post<ApiResponse<{ id: number; pan_url: string; pan_code: string | null; share_id: string | null }>>(`${PREFIX}/${id}/upload-pan`, {})
}

/** 触发浏览器接管下载该资料，成功后自动上传网盘并生成分享链接（全程 1-5 分钟，放大超时） */
export const downloadMaterialPoolFile = async (id: number): Promise<ApiResponse<MaterialPoolItem>> => {
  return post<ApiResponse<MaterialPoolItem>>(`${PREFIX}/${id}/download`, {}, { timeout: 600000 })
}

export const listMaterialPoolGoods = async (id: number, accountId: string, category?: MaterialPoolCategory, publishOptions?: MaterialPoolPublishOptions): Promise<ApiResponse<MaterialPoolItem>> => {
  return post<ApiResponse<MaterialPoolItem>>(`${PREFIX}/${id}/list`, { account_id: accountId, category, publish_options: publishOptions })
}

export const batchListMaterialPoolGoods = async (ids: number[], accountId: string, category?: MaterialPoolCategory, publishOptions?: MaterialPoolPublishOptions): Promise<ApiResponse<{ results: Array<{ id: number; success: boolean; message?: string }>; success_count: number; total: number }>> => {
  return post<ApiResponse<{ results: Array<{ id: number; success: boolean; message?: string }>; success_count: number; total: number }>>(`${PREFIX}/batch-list`, { ids, account_id: accountId, category, publish_options: publishOptions })
}

/** 上传封面图（复用商品发布图片上传接口，返回 {本地路径, 预览URL}） */
export const uploadMaterialPoolCover = async (file: File): Promise<{ path: string; url: string }> => {
  const formData = new FormData()
  formData.append('files', file)
  const res = await post<ApiResponse<{ paths?: string[]; urls?: string[] }>>('/api/v1/product-publish/upload/images', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  const path = res.data?.paths?.[0]
  const url = res.data?.urls?.[0]
  if (!res.success || !path) {
    throw new Error(res.message || '封面上传失败')
  }
  return { path, url: url || '' }
}

/** 把 cover_image 存储值转成可预览的 URL（绝对路径或 /static/ 相对路径均可） */
export const coverImageToUrl = (value: string | null | undefined): string => {
  const preview = toPreviewUrl(value)
  if (!preview) return ''
  if (preview.startsWith('/static/') || /^(https?:|blob:|data:)/i.test(preview)) return preview
  const filename = preview.split('/').pop()
  return filename ? `/static/uploads/products/${filename}` : preview
}
