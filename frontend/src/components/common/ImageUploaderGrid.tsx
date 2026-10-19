/**
 * 商品图片网格公共组件。
 *
 * 统一素材库 / 商品发布 / 商品编辑等页面的图片上传展示交互：
 * - 缩略图网格，第一张标记"首图"（数组顺序即发布顺序）
 * - hover 显示删除按钮；点击缩略图放大查看（弹出层支持左右切换、Esc 关闭）
 * - 拖拽调整顺序（@dnd-kit，鼠标/触摸均可）
 * - readonly 模式（如素材详情查看）：不显示删除/拖拽/上传入口，仅可放大查看
 *
 * 层级注意：放大层使用 z-[500]，高于现有业务弹窗遮罩（z-200）与 Toast（z-999 之下）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { DragEndEvent } from '@dnd-kit/core'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, GripVertical, Loader2, Trash2, Upload, X, ZoomIn } from 'lucide-react'
import clsx from 'clsx'

interface ImageLightboxProps {
  images: string[]
  index: number
  toUrl: (path: string) => string
  onClose: () => void
}

/** 图片放大查看弹出层：左右切换、张数指示、Esc / 点击遮罩 / × 关闭。 */
function ImageLightbox({ images, index, toUrl, onClose }: ImageLightboxProps) {
  const [current, setCurrent] = useState(index)
  const total = images.length

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowLeft') setCurrent((i) => (i - 1 + total) % total)
      if (event.key === 'ArrowRight') setCurrent((i) => (i + 1) % total)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose, total])

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      className="fixed inset-0 z-[500] flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
    >
      {/* 张数指示 */}
      <span className="absolute left-1/2 top-4 -translate-x-1/2 rounded-full bg-black/60 px-3 py-1 text-xs text-white">
        {current + 1} / {total}
      </span>
      {/* 关闭 */}
      <button
        type="button"
        title="关闭 (Esc)"
        onClick={onClose}
        className="absolute right-4 top-4 rounded-full bg-black/60 p-2 text-white transition-colors hover:bg-red-500"
      >
        <X className="h-5 w-5" />
      </button>
      {/* 上一张 */}
      {total > 1 && (
        <button
          type="button"
          title="上一张 (←)"
          onClick={(event) => {
            event.stopPropagation()
            setCurrent((i) => (i - 1 + total) % total)
          }}
          className="absolute left-4 top-1/2 -translate-y-1/2 rounded-full bg-black/60 p-2 text-white transition-colors hover:bg-black/80"
        >
          <ChevronLeft className="h-6 w-6" />
        </button>
      )}
      <motion.img
        key={current}
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.15 }}
        src={toUrl(images[current])}
        alt={`图片 ${current + 1}`}
        className="max-h-[85vh] max-w-[90vw] rounded-lg object-contain shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      />
      {/* 下一张 */}
      {total > 1 && (
        <button
          type="button"
          title="下一张 (→)"
          onClick={(event) => {
            event.stopPropagation()
            setCurrent((i) => (i + 1) % total)
          }}
          className="absolute right-4 top-1/2 -translate-y-1/2 rounded-full bg-black/60 p-2 text-white transition-colors hover:bg-black/80"
        >
          <ChevronRight className="h-6 w-6" />
        </button>
      )}
    </motion.div>
  )
}

interface SortableImageProps {
  id: string
  url: string
  index: number
  readonly: boolean
  itemClassName: string
  toUrl: (path: string) => string
  onRemove: (() => void) | null
  onPreview: () => void
}

function SortableImage({ id, url, index, readonly, itemClassName, toUrl, onRemove, onPreview }: SortableImageProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
    disabled: readonly,
  })
  // 拖拽与点击共用同一元素：位移超过阈值视为拖拽，抑制随后的 click 触发放大
  const pointerStart = useRef<{ x: number; y: number } | null>(null)
  const suppressClick = useRef(false)

  const handleClick = useCallback(() => {
    if (suppressClick.current) {
      suppressClick.current = false
      return
    }
    onPreview()
  }, [onPreview])

  // 注意：显式 onPointerDown 会覆盖 {...listeners} 中 dnd-kit 的同名校验，
  // 必须手动转发，否则 PointerSensor 永远收不到 pointerdown，拖拽无法激活
  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    pointerStart.current = { x: event.clientX, y: event.clientY }
    listeners?.onPointerDown?.(event)
  }

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      {...attributes}
      {...(readonly ? {} : listeners)}
      onPointerDown={handlePointerDown}
      onPointerUp={(event) => {
        const start = pointerStart.current
        pointerStart.current = null
        if (!start) return
        const distance = Math.hypot(event.clientX - start.x, event.clientY - start.y)
        if (distance > 6) suppressClick.current = true
      }}
      onClick={handleClick}
      className={clsx(
        'group relative overflow-hidden rounded-lg border border-slate-200 dark:border-slate-600',
        itemClassName,
        !readonly && 'cursor-grab active:cursor-grabbing',
        isDragging && 'z-30 border-blue-400 opacity-90 shadow-lg',
      )}
    >
      <img src={toUrl(url)} alt={`图片 ${index + 1}`} draggable={false} className="h-full w-full object-cover" />
      {index === 0 && (
        <span className="absolute bottom-0 left-0 right-0 bg-blue-500/80 py-0.5 text-center text-[10px] text-white">
          首图
        </span>
      )}
      {!readonly && (
        <>
          {/* 放大入口 */}
          <button
            type="button"
            title="放大查看"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation()
              onPreview()
            }}
            className="absolute left-0.5 top-0.5 rounded bg-black/60 p-0.5 text-white opacity-0 transition-opacity group-hover:opacity-100 hover:bg-blue-500"
          >
            <ZoomIn className="h-3 w-3" />
          </button>
          {/* 删除 */}
          {onRemove && (
            <button
              type="button"
              title="移除图片"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation()
                onRemove()
              }}
              className="absolute right-0.5 top-0.5 rounded bg-black/60 p-0.5 text-white opacity-0 transition-opacity group-hover:opacity-100 hover:bg-red-500"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
          {/* 拖拽提示 */}
          <span className="absolute bottom-0.5 right-0.5 rounded bg-black/50 p-0.5 text-white/80 opacity-0 transition-opacity group-hover:opacity-100">
            <GripVertical className="h-3 w-3" />
          </span>
        </>
      )}
    </div>
  )
}

export interface ImageUploaderGridProps {
  /** 图片路径/URL 数组，顺序即发布顺序 */
  images: string[]
  /** 路径 → 预览 URL 的转换，缺省为原样使用 */
  toUrl?: (path: string) => string
  /** 顺序或内容变化时回传新数组（删除、拖拽排序统一走这里） */
  onChange?: (next: string[]) => void
  /** 选择文件后回调（提供则显示添加入口） */
  onUploadFiles?: (files: File[]) => void
  uploading?: boolean
  /** 最多图片数，默认 9 */
  maxCount?: number
  /** 只读模式：仅展示 + 放大，无删除/拖拽/上传 */
  readonly?: boolean
  /** 缩略图尺寸类名，默认 h-20 w-20 */
  itemClassName?: string
  /** 空列表占位文案 */
  emptyText?: string
  className?: string
}

export function ImageUploaderGrid({
  images,
  toUrl = (path) => path,
  onChange,
  onUploadFiles,
  uploading = false,
  maxCount = 9,
  readonly = false,
  itemClassName = 'h-20 w-20',
  emptyText,
  className,
}: ImageUploaderGridProps) {
  const [previewIndex, setPreviewIndex] = useState<number | null>(null)

  const sensors = useSensors(
    // 位移 6px 以内视为点击（配合放大），超过则进入拖拽
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const ids = images.map((url, index) => `${url}#${index}`)
  const idOf = (index: number) => ids[index]

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || !onChange) return
    const from = ids.indexOf(String(active.id))
    const to = ids.indexOf(String(over.id))
    if (from < 0 || to < 0 || from === to) return
    onChange(arrayMove(images, from, to))
  }

  const handleRemove = (index: number) => {
    if (!onChange) return
    onChange(images.filter((_, itemIndex) => itemIndex !== index))
  }

  return (
    <>
      <div className={clsx('flex flex-wrap gap-2', className)}>
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext items={ids} strategy={rectSortingStrategy}>
            {images.map((url, index) => (
              <SortableImage
                key={idOf(index)}
                id={idOf(index)}
                url={url}
                index={index}
                readonly={readonly}
                itemClassName={itemClassName}
                toUrl={toUrl}
                onRemove={readonly || !onChange ? null : () => handleRemove(index)}
                onPreview={() => setPreviewIndex(index)}
              />
            ))}
          </SortableContext>
        </DndContext>
        {!readonly && onUploadFiles && images.length < maxCount && (
          <label
            className={clsx(
              'flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 text-slate-400 transition-colors hover:border-blue-400 hover:text-blue-500 dark:border-slate-600',
              itemClassName,
              uploading && 'pointer-events-none opacity-50',
            )}
          >
            {uploading ? <Loader2 className="h-5 w-5 animate-spin" /> : <Upload className="h-5 w-5" />}
            <span className="mt-1 text-xs">{uploading ? '上传中' : '添加图片'}</span>
            <input
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              disabled={uploading}
              onChange={(event) => {
                const files = Array.from(event.target.files || [])
                event.target.value = ''
                if (files.length) onUploadFiles(files)
              }}
            />
          </label>
        )}
        {images.length === 0 && emptyText && <span className="text-sm text-slate-400">{emptyText}</span>}
      </div>
      <AnimatePresence>
        {previewIndex !== null && images[previewIndex] !== undefined && (
          <ImageLightbox images={images} index={previewIndex} toUrl={toUrl} onClose={() => setPreviewIndex(null)} />
        )}
      </AnimatePresence>
    </>
  )
}

export default ImageUploaderGrid
