/**
 * 网盘同步日志 API 接口层
 *
 * 数据来自 xy_pan_sync_logs：每次网盘互传任务结束落一条（成功/失败都记）。
 * 运行中任务的实时进度走 panBrowser.ts 的 getPanSyncStatus（内存 registry）。
 */
import { del, get } from '@/utils/request'
import type { ApiResponse } from '@/types'

const PREFIX = '/api/v1/admin'

/** 一条网盘同步任务日志 */
export interface PanSyncLogItem {
  id: number
  task_id: string
  src_type: string
  dst_type: string
  remote: string
  is_dir: boolean
  /** done / failed */
  status: string
  total: number
  processed: number
  uploaded: number
  skipped: number
  failed: number
  /** JSON 字符串数组，最多 10 条单文件错误 */
  errors: string | null
  duration_seconds: number | null
  created_at: string | null
}

export interface PanSyncLogListData {
  total: number
  page: number
  page_size: number
  list: PanSyncLogItem[]
}

/** 分页查询同步日志（按创建时间倒序） */
export const getPanSyncLogs = async (params: {
  status?: string
  pan_type?: string
  page?: number
  page_size?: number
}): Promise<ApiResponse<PanSyncLogListData>> => {
  const search = new URLSearchParams()
  if (params.status) search.set('status', params.status)
  if (params.pan_type) search.set('pan_type', params.pan_type)
  search.set('page', String(params.page || 1))
  search.set('page_size', String(params.page_size || 20))
  return get<ApiResponse<PanSyncLogListData>>(`${PREFIX}/pan-sync-logs?${search.toString()}`)
}

/** 清空全部同步日志 */
export const clearPanSyncLogs = async (): Promise<ApiResponse<null>> => {
  return del<ApiResponse<null>>(`${PREFIX}/pan-sync-logs/clear`)
}
