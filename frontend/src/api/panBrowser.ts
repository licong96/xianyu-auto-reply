/**
 * 网盘浏览 API 接口层 - 多网盘并列查询与合并列表方案
 *
 * 功能：
 * 1. 各网盘连接状态（逐盘检测，某盘失败不影响其他盘）
 * 2. 目录内容列表（多盘并列查询、按文件名合并成一份单层列表）
 * 3. 创建分享（body 带 locations，逐盘分享、逐盘查重）
 * 4. 导入资料池（一条记录挂多个网盘链接，写入子表）
 */
import { get, post } from '@/utils/request'
import type { ApiResponse } from '@/types'
import type { MaterialPoolItem } from './materialPool'

const PREFIX = '/api/v1/pan'

// ==================== 类型定义 ====================

/** 一个文件在某个网盘上的位置 */
export interface PanLocation {
  pan_type: string
  path: string
  fid: string | null
  size: number | null
  mtime: number | null
  /** 资料池导入状态：null=未导入 */
  pool: { pool_id: number; status: string } | null
}

/** 合并后的一行：文件名相同的各盘收成一行 */
export interface PanEntry {
  file_name: string
  dir: boolean
  /** 同名文件在各盘大小不一致（默认不双盘发货，须人工勾选） */
  size_mismatch: boolean
  locations: PanLocation[]
}

/** 网盘连接状态 */
export interface PanDriveStatus {
  pan_type: string
  ok: boolean
  error?: string
}

/** 单盘分享结果 */
export interface PanShareItem {
  pan_type: string
  ok: boolean
  share_url?: string | null
  pan_code?: string | null
  share_id?: string | null
  share_expire_at?: string | null
  error?: string
}

// ==================== 接口请求 ====================

/** 各网盘连接状态 */
export const getPanStatus = async (): Promise<ApiResponse<{ drives: PanDriveStatus[] }>> => {
  return get<ApiResponse<{ drives: PanDriveStatus[] }>>(`${PREFIX}/status`)
}

/** 列出指定目录内容（多盘并列 + 合并） */
export const getPanEntries = async (
  path: string
): Promise<ApiResponse<{ path: string; drives: PanDriveStatus[]; list: PanEntry[] }>> => {
  return get<ApiResponse<{ path: string; drives: PanDriveStatus[]; list: PanEntry[] }>>(
    `${PREFIX}/entries?path=${encodeURIComponent(path)}`
  )
}

/** 创建分享（逐盘分享、逐盘查重；单侧失败可只重试该侧） */
export const createPanShare = async (
  locations: { pan_type: string; path: string; fid?: string | null }[]
): Promise<ApiResponse<{ shares: PanShareItem[] }>> => {
  return post<ApiResponse<{ shares: PanShareItem[] }>>(`${PREFIX}/share`, { locations })
}

/** 把网盘文件/文件夹导入资料池（幂等：命中已有记录则补链） */
export const importPanFile = async (data: {
  file_name: string
  file_size?: number
  is_dir?: boolean
  create_share?: boolean
  locations: { pan_type: string; path: string; fid?: string | null; size?: number | null }[]
}): Promise<ApiResponse<MaterialPoolItem>> => {
  return post<ApiResponse<MaterialPoolItem>>(`${PREFIX}/import`, data)
}
