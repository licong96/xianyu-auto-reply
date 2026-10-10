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
import type { ApiResponse } from '@/types'

const PREFIX = '/api/v1/material-pool'

// ==================== 类型定义 ====================

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
  share_expire_at: string | null
  title: string | null
  description: string | null
  price: number | null
  cover_image: string | null
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
  page?: number
  page_size?: number
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

export const listMaterialPoolGoods = async (id: number, accountId: string): Promise<ApiResponse<MaterialPoolItem>> => {
  return post<ApiResponse<MaterialPoolItem>>(`${PREFIX}/${id}/list`, { account_id: accountId })
}

export const batchListMaterialPoolGoods = async (ids: number[], accountId: string): Promise<ApiResponse<{ results: Array<{ id: number; success: boolean; message?: string }>; success_count: number; total: number }>> => {
  return post<ApiResponse<{ results: Array<{ id: number; success: boolean; message?: string }>; success_count: number; total: number }>>(`${PREFIX}/batch-list`, { ids, account_id: accountId })
}

/** 上传封面图（复用通用图片上传接口，返回图片 URL） */
export const uploadMaterialPoolCover = async (file: File): Promise<string> => {
  const formData = new FormData()
  formData.append('image', file)
  const res = await post<ApiResponse<{ url?: string; image_url?: string }>>('/api/v1/upload/upload-image', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  const url = res.data?.url || res.data?.image_url
  if (!res.success || !url) {
    throw new Error(res.message || '封面上传失败')
  }
  return url
}
