/**
 * 把上传接口返回的本机路径、或已保存的绝对路径，转成浏览器能请求的地址。
 * 本地服务通过 /static 提供 backend-web/static 下的文件。
 */
export function toPreviewUrl(value: string | null | undefined): string {
  if (!value) return ''
  const normalized = value.replace(/\\/g, '/')
  if (/^(https?:|blob:|data:)/i.test(normalized)) return normalized
  const marker = '/static/'
  const index = normalized.indexOf(marker)
  if (index >= 0) return normalized.slice(index)
  if (normalized.startsWith('static/')) return `/${normalized}`
  return normalized
}
