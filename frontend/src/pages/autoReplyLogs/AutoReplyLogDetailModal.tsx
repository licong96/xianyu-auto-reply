import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Bot, Check, Copy, Loader2, X } from 'lucide-react'
import {
  getAutoReplyLogDetail,
  type AutoReplyLogDetail,
  type AutoReplyPromptSnapshot,
} from '@/api/autoReplyLogs'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'

interface Props {
  isOpen: boolean
  logId: number | null
  onClose: () => void
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
    second: '2-digit',
    hour12: false,
  })
}

function InfoRow({ label, value }: { label: string; value?: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-1.5 text-sm">
      <span className="w-24 flex-shrink-0 text-slate-500 dark:text-slate-400">{label}</span>
      <span className="flex-1 whitespace-pre-wrap break-all text-slate-900 dark:text-slate-100">{value ?? '-'}</span>
    </div>
  )
}

function PromptBlock({ title, content, copied, onCopy }: {
  title: string
  content: string
  copied: boolean
  onCopy: (text: string) => void
}) {
  if (!content) return null
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-slate-50 dark:bg-slate-800/60 border-b border-slate-200 dark:border-slate-700">
        <span className="text-xs font-medium text-slate-600 dark:text-slate-300">{title}</span>
        <button
          onClick={() => onCopy(content)}
          className="flex items-center gap-1 text-xs text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300"
        >
          {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
          复制
        </button>
      </div>
      <pre className="px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap break-words max-h-72 overflow-y-auto bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200">
        {content}
      </pre>
    </div>
  )
}

export function AutoReplyLogDetailModal({ isOpen, logId, onClose }: Props) {
  const { addToast } = useUIStore()
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<AutoReplyLogDetail | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!isOpen || !logId) {
      setDetail(null)
      return
    }
    let cancelled = false
    const load = async () => {
      try {
        setLoading(true)
        const result = await getAutoReplyLogDetail(logId)
        if (cancelled) return
        if (result.success && result.data) {
          setDetail(result.data)
        } else {
          addToast({ type: 'error', message: result.message || '加载日志详情失败' })
          onClose()
        }
      } catch (error) {
        if (cancelled) return
        addToast({ type: 'error', message: getApiErrorMessage(error, '加载日志详情失败') })
        onClose()
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [isOpen, logId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      addToast({ type: 'error', message: '复制失败' })
    }
  }

  const snapshot: AutoReplyPromptSnapshot | null = detail?.prompt_snapshot ?? null
  const contextInjected = snapshot?.context_injected ?? []
  const hasPromptSnapshot = !!snapshot && !!snapshot.system_prompt

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
            className="relative w-full max-w-3xl mx-4 max-h-[85vh] flex flex-col bg-white dark:bg-slate-800 rounded-2xl shadow-2xl border border-slate-200 dark:border-slate-700"
          >
            <button
              onClick={onClose}
              className="absolute top-3 right-3 z-10 p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="px-6 pt-5 pb-3 flex items-center gap-2 border-b border-slate-200 dark:border-slate-700">
              <Bot className="w-5 h-5 text-blue-500" />
              <h3 className="text-lg font-semibold text-slate-900 dark:text-white">消息日志详情</h3>
              {detail ? (
                <span className="text-xs text-slate-400">#{detail.id}</span>
              ) : null}
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-5">
              {loading ? (
                <div className="flex justify-center py-12">
                  <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
                </div>
              ) : !detail ? null : (
                <>
                  {/* 基本信息 */}
                  <section>
                    <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-2">基本信息</h4>
                    <div className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-1">
                      <InfoRow label="账号" value={detail.account_name ? `${detail.account_name}（${detail.account_id}）` : detail.account_id} />
                      <InfoRow label="买家" value={detail.sender_user_name ? `${detail.sender_user_name}（${detail.sender_user_id}）` : detail.sender_user_id} />
                      <InfoRow label="商品" value={detail.item_title ? `${detail.item_title}${detail.item_id ? `（${detail.item_id}）` : ''}` : (detail.item_id ?? '-')} />
                      <InfoRow label="策略" value={detail.reply_strategy} />
                      <InfoRow label="AI 模型" value={detail.ai_provider_name ? `${detail.ai_provider_name} / ${detail.ai_model_name ?? '-'}` : (detail.ai_model_name ?? '-')} />
                      <InfoRow label="发送状态" value={detail.send_status} />
                      <InfoRow label="时间" value={formatDateTime(detail.created_at)} />
                    </div>
                  </section>

                  {/* 对话内容 */}
                  <section>
                    <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-2">对话内容</h4>
                    <div className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2">
                      <InfoRow label="买家消息" value={detail.source_message} />
                      <InfoRow label="AI 回复" value={detail.reply_text} />
                      {detail.error_message ? <InfoRow label="错误信息" value={<span className="text-red-600 dark:text-red-400">{detail.error_message}</span>} /> : null}
                    </div>
                  </section>

                  {/* AI 提示词快照 */}
                  <section>
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-200">AI 提示词快照</h4>
                      {snapshot?.model_name ? (
                        <span className="text-xs text-slate-500 dark:text-slate-400">
                          {snapshot.provider_name ?? snapshot.provider_type} / {snapshot.model_name}
                        </span>
                      ) : null}
                    </div>

                    {!hasPromptSnapshot ? (
                      <div className="rounded-lg border border-dashed border-slate-300 dark:border-slate-600 px-4 py-6 text-center text-sm text-slate-500 dark:text-slate-400">
                        该记录无 AI 提示词快照（可能非 AI 回复，或为升级前产生的历史数据）
                      </div>
                    ) : (
                      <div className="space-y-3">
                        <div className="flex flex-wrap gap-2 text-xs">
                          {snapshot?.default_shop_prompt_used ? (
                            <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                              店铺说明使用内置默认
                            </span>
                          ) : null}
                          {snapshot?.custom_global_rules ? (
                            <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                              含店铺自定义规则
                            </span>
                          ) : null}
                          {snapshot?.transport_note ? (
                            <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                              {snapshot.transport_note}
                            </span>
                          ) : null}
                        </div>

                        <PromptBlock
                          title="System Prompt（完整生效版：统一话术 + 全局规则 + 店铺说明 + 自定义规则）"
                          content={snapshot?.system_prompt ?? ''}
                          copied={copied}
                          onCopy={handleCopy}
                        />
                        <PromptBlock
                          title="User Prompt（商品信息 + 对话历史 + 买家消息）"
                          content={snapshot?.user_prompt ?? ''}
                          copied={copied}
                          onCopy={handleCopy}
                        />

                        {snapshot?.shop_prompt ? (
                          <PromptBlock title="店铺说明（本次实际使用）" content={snapshot.shop_prompt} copied={copied} onCopy={handleCopy} />
                        ) : null}

                        {snapshot?.item_info ? (
                          <details className="rounded-lg border border-slate-200 dark:border-slate-700">
                            <summary className="px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-300 cursor-pointer bg-slate-50 dark:bg-slate-800/60">
                              商品信息快照
                            </summary>
                            <pre className="px-3 py-2 text-xs whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                              {JSON.stringify(snapshot.item_info, null, 2)}
                            </pre>
                          </details>
                        ) : null}

                        {contextInjected.length > 0 ? (
                          <details className="rounded-lg border border-slate-200 dark:border-slate-700">
                            <summary className="px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-300 cursor-pointer bg-slate-50 dark:bg-slate-800/60">
                              注入的对话历史（{contextInjected.length} 条）
                            </summary>
                            <div className="px-3 py-2 space-y-1.5 max-h-56 overflow-y-auto">
                              {contextInjected.map((msg, index) => (
                                <div key={index} className="text-xs">
                                  <span className={msg.role === 'assistant' ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-slate-500 dark:text-slate-400 font-medium'}>
                                    {msg.role === 'assistant' ? '客服(AI)' : '买家'}：
                                  </span>
                                  <span className="whitespace-pre-wrap break-words text-slate-800 dark:text-slate-200">{msg.content}</span>
                                </div>
                              ))}
                            </div>
                          </details>
                        ) : null}

                        {snapshot?.request_messages ? (
                          <details className="rounded-lg border border-slate-200 dark:border-slate-700">
                            <summary className="px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-300 cursor-pointer bg-slate-50 dark:bg-slate-800/60">
                              实际请求消息（按服务商协议组装）
                            </summary>
                            <pre className="px-3 py-2 text-xs whitespace-pre-wrap break-all max-h-56 overflow-y-auto">
                              {JSON.stringify(snapshot.request_messages, null, 2)}
                            </pre>
                          </details>
                        ) : null}
                      </div>
                    )}
                  </section>
                </>
              )}
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}
