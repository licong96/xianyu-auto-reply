/**
 * 分享续期巡检日志 API 接口层
 *
 * 数据来自 xy_share_check_logs：
 * - task_type=renew：到期续期/手动重新分享的逐条结果
 * - task_type=check：内容存活巡检的逐条结果（真实浏览器打开分享页判定）
 */
import { del, get } from '@/utils/request'
import type { ApiResponse } from '@/types'

const PREFIX = '/api/v1/admin'

/** 一条分享续期/巡检日志 */
export interface ShareCheckLogItem {
  id: number
  /** renew=续期 / check=巡检 */
  task_type: string
  pool_id: number | null
  pan_type: string
  pan_url: string | null
  old_url: string | null
  /** renew: done/failed；check: valid/invalid/empty/error/risk */
  result: string
  detail: string | null
  fail_reason: string | null
  duration_seconds: number | null
  created_at: string | null
}

export interface ShareCheckLogListData {
  total: number
  page: number
  page_size: number
  list: ShareCheckLogItem[]
}

/** 分页查询分享续期/巡检日志（按创建时间倒序） */
export const getShareCheckLogs = async (params: {
  task_type?: string
  result?: string
  pan_type?: string
  pool_id?: number
  page?: number
  page_size?: number
}): Promise<ApiResponse<ShareCheckLogListData>> => {
  const search = new URLSearchParams()
  if (params.task_type) search.set('task_type', params.task_type)
  if (params.result) search.set('result', params.result)
  if (params.pan_type) search.set('pan_type', params.pan_type)
  if (params.pool_id) search.set('pool_id', String(params.pool_id))
  search.set('page', String(params.page || 1))
  search.set('page_size', String(params.page_size || 20))
  return get<ApiResponse<ShareCheckLogListData>>(`${PREFIX}/share-check-logs?${search.toString()}`)
}

/** 清空全部分享续期/巡检日志 */
export const clearShareCheckLogs = async (): Promise<ApiResponse<null>> => {
  return del<ApiResponse<null>>(`${PREFIX}/share-check-logs/clear`)
}
