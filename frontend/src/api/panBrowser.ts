/**
 * 网盘浏览 API 接口层 - 网盘浏览与直选上架
 *
 * 功能：
 * 1. 网盘连接状态检测（凭证失效时明确提示）
 * 2. 目录内容列表（单层下钻，附资料池导入状态）
 * 3. 创建分享（后端查重，不重复创建）
 * 4. 导入资料池（幂等，复用 xy_material_pool，source_type=quark-manual）
 */
import { get, post } from '@/utils/request'
import type { ApiResponse } from '@/types'
import type { MaterialPoolItem } from './materialPool'

const PREFIX = '/api/v1/pan'

// ==================== 类型定义 ====================

export interface PanEntry {
  fid: string
  file_name: string
  path: string
  size: number
  dir: boolean
  mtime: number
  download_url: string
  /** 资料池导入状态：null=未导入 */
  pool: { pool_id: number; status: string } | null
}

export interface PanShareResult {
  share_url: string | null
  pan_code: string | null
  share_id: string | null
  share_expire_at: string | null
}

// ==================== 接口请求 ====================

/** 网盘连接状态（失败说明凭证失效或 kuake 不可用） */
export const getPanStatus = async (): Promise<ApiResponse<{ nickname?: string }>> => {
  return get<ApiResponse<{ nickname?: string }>>(`${PREFIX}/status`)
}

/** 列出指定目录内容（单层） */
export const getPanEntries = async (path: string): Promise<ApiResponse<{ path: string; list: PanEntry[] }>> => {
  return get<ApiResponse<{ path: string; list: PanEntry[] }>>(`${PREFIX}/entries?path=${encodeURIComponent(path)}`)
}

/** 创建分享（后端按资料池记录 + first_fid 双重查重） */
export const createPanShare = async (path: string, fid?: string): Promise<ApiResponse<PanShareResult>> => {
  return post<ApiResponse<PanShareResult>>(`${PREFIX}/share`, { path, fid })
}

/** 把网盘文件导入资料池（幂等：已存在直接返回旧记录） */
export const importPanFile = async (data: {
  pan_path: string
  file_name: string
  file_size?: number
  fid?: string
  create_share?: boolean
}): Promise<ApiResponse<MaterialPoolItem>> => {
  return post<ApiResponse<MaterialPoolItem>>(`${PREFIX}/import`, data)
}
