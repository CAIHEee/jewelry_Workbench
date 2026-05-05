import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { createPortal } from "react-dom";

type MarkupTool = "mask" | "doodle" | "text" | "move";

interface MarkupPoint {
  x: number;
  y: number;
}

interface MarkupPath {
  id: string;
  tool: "mask" | "doodle";
  color: string;
  size: number;
  points: MarkupPoint[];
}

interface MarkupText {
  id: string;
  text: string;
  x: number;
  y: number;
  color: string;
  size: number;
}

interface LocalImageMarkupEditorProps {
  sourceUrl: string | null;
  sourceName?: string | null;
  disabled?: boolean;
  onEditedFileChange: (file: File | null, previewUrl: string | null) => void;
}

const MARKUP_FILE_NAME = "local-refine-markup.png";

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function fileFromBlob(blob: Blob) {
  return new File([blob], MARKUP_FILE_NAME, { type: "image/png" });
}

export function LocalImageMarkupEditor({
  sourceUrl,
  sourceName,
  disabled,
  onEditedFileChange,
}: LocalImageMarkupEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const currentPathRef = useRef<MarkupPath | null>(null);
  const dragTextRef = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const [open, setOpen] = useState(false);
  const [imageReady, setImageReady] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tool, setTool] = useState<MarkupTool>("mask");
  const [paths, setPaths] = useState<MarkupPath[]>([]);
  const [texts, setTexts] = useState<MarkupText[]>([]);
  const [selectedTextId, setSelectedTextId] = useState<string | null>(null);
  const [brushSize, setBrushSize] = useState(28);
  const [brushCursor, setBrushCursor] = useState({ x: 0, y: 0, size: 28, visible: false });
  const [markupPreviewUrl, setMarkupPreviewUrl] = useState<string | null>(null);
  const selectedText = useMemo(() => texts.find((item) => item.id === selectedTextId) ?? null, [selectedTextId, texts]);

  useEffect(() => {
    setPaths([]);
    setTexts([]);
    setSelectedTextId(null);
    setImageReady(false);
    setLoadError(null);
    clearEditedFile();
  }, [sourceUrl]);

  useEffect(() => {
    return () => {
      if (markupPreviewUrl) URL.revokeObjectURL(markupPreviewUrl);
    };
  }, [markupPreviewUrl]);

  useEffect(() => {
    if (!open || !sourceUrl) return;
    const image = new Image();
    let retriedWithoutCors = false;
    image.onload = () => {
      imageRef.current = image;
      const canvas = canvasRef.current;
      if (canvas) {
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
      }
      setImageReady(true);
      setLoadError(null);
      drawCanvas(image, paths, texts);
    };
    image.onerror = () => {
      if (!retriedWithoutCors) {
        retriedWithoutCors = true;
        image.removeAttribute("crossorigin");
        image.src = sourceUrl;
        return;
      }
      setImageReady(false);
      setLoadError("参考图加载失败，请换一张图片或使用本地上传。");
    };
    image.crossOrigin = "anonymous";
    image.src = sourceUrl;
  }, [open, sourceUrl]);

  useEffect(() => {
    if (!open || !imageReady || !imageRef.current) return;
    drawCanvas(imageRef.current, paths, texts);
  }, [open, imageReady, paths, texts, selectedTextId]);

  function clearEditedFile() {
    setMarkupPreviewUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return null;
    });
    onEditedFileChange(null, null);
  }

  function getCanvasPoint(event: PointerEvent<HTMLCanvasElement>): MarkupPoint {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
    };
  }

  function getActiveBrushSize() {
    return tool === "doodle" ? Math.max(4, Math.round(brushSize * 0.38)) : brushSize;
  }

  function updateBrushCursor(event: PointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    const wrap = canvas?.parentElement;
    if (!canvas || !wrap || (tool !== "mask" && tool !== "doodle")) {
      setBrushCursor((current) => ({ ...current, visible: false }));
      return;
    }
    const canvasRect = canvas.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    const scale = canvas.width > 0 ? canvasRect.width / canvas.width : 1;
    setBrushCursor({
      x: event.clientX - wrapRect.left + wrap.scrollLeft,
      y: event.clientY - wrapRect.top + wrap.scrollTop,
      size: Math.max(6, getActiveBrushSize() * scale),
      visible: true,
    });
  }

  function hideBrushCursor() {
    setBrushCursor((current) => ({ ...current, visible: false }));
  }

  function drawCanvas(image: HTMLImageElement, nextPaths: MarkupPath[], nextTexts: MarkupText[]) {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    nextPaths.forEach((path) => {
      if (path.points.length < 1) return;
      context.save();
      context.lineCap = "round";
      context.lineJoin = "round";
      context.lineWidth = path.size;
      context.strokeStyle = path.color;
      context.globalAlpha = path.tool === "mask" ? 0.38 : 0.92;
      context.beginPath();
      context.moveTo(path.points[0].x, path.points[0].y);
      path.points.slice(1).forEach((point) => context.lineTo(point.x, point.y));
      context.stroke();
      context.restore();
    });

    nextTexts.forEach((item) => {
      context.save();
      context.font = `700 ${item.size}px sans-serif`;
      context.textBaseline = "top";
      context.lineJoin = "round";
      context.strokeStyle = "rgba(0, 0, 0, 0.78)";
      context.lineWidth = Math.max(4, item.size * 0.16);
      context.strokeText(item.text, item.x, item.y);
      context.fillStyle = item.color;
      context.fillText(item.text, item.x, item.y);
      if (item.id === selectedTextId) {
        const metrics = context.measureText(item.text);
        context.strokeStyle = "rgba(242, 202, 72, 0.9)";
        context.lineWidth = 2;
        context.strokeRect(item.x - 6, item.y - 6, metrics.width + 12, item.size + 12);
      }
      context.restore();
    });
  }

  function hitTestText(point: MarkupPoint) {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!context) return null;
    for (const item of [...texts].reverse()) {
      context.font = `700 ${item.size}px sans-serif`;
      const width = context.measureText(item.text).width;
      if (point.x >= item.x - 8 && point.x <= item.x + width + 8 && point.y >= item.y - 8 && point.y <= item.y + item.size + 12) {
        return item;
      }
    }
    return null;
  }

  function handlePointerDown(event: PointerEvent<HTMLCanvasElement>) {
    if (!imageReady) return;
    updateBrushCursor(event);
    const canvas = canvasRef.current;
    canvas?.setPointerCapture(event.pointerId);
    const point = getCanvasPoint(event);

    if (tool === "text") {
      const newText: MarkupText = {
        id: makeId("text"),
        text: "修改这里",
        x: point.x,
        y: point.y,
        color: "#f2ca48",
        size: Math.max(22, Math.round((canvas?.width ?? 1200) / 34)),
      };
      setTexts((current) => [...current, newText]);
      setSelectedTextId(newText.id);
      setTool("move");
      return;
    }

    const hitText = hitTestText(point);
    if (tool === "move" || hitText) {
      if (hitText) {
        setSelectedTextId(hitText.id);
        dragTextRef.current = { id: hitText.id, dx: point.x - hitText.x, dy: point.y - hitText.y };
      }
      return;
    }

    const path: MarkupPath = {
      id: makeId(tool),
      tool: tool === "doodle" ? "doodle" : "mask",
      color: tool === "doodle" ? "#f2ca48" : "#ff3b5f",
      size: tool === "doodle" ? Math.max(4, Math.round(brushSize * 0.38)) : brushSize,
      points: [point],
    };
    currentPathRef.current = path;
    setPaths((current) => [...current, path]);
  }

  function handlePointerMove(event: PointerEvent<HTMLCanvasElement>) {
    updateBrushCursor(event);
    const point = getCanvasPoint(event);
    if (dragTextRef.current) {
      const drag = dragTextRef.current;
      setTexts((current) =>
        current.map((item) =>
          item.id === drag.id
            ? {
                ...item,
                x: point.x - drag.dx,
                y: point.y - drag.dy,
              }
            : item,
        ),
      );
      return;
    }
    const currentPath = currentPathRef.current;
    if (!currentPath) return;
    currentPath.points = [...currentPath.points, point];
    setPaths((current) => current.map((item) => (item.id === currentPath.id ? { ...currentPath } : item)));
  }

  function handlePointerUp(event: PointerEvent<HTMLCanvasElement>) {
    canvasRef.current?.releasePointerCapture(event.pointerId);
    currentPathRef.current = null;
    dragTextRef.current = null;
  }

  function updateSelectedText(value: string) {
    if (!selectedTextId) return;
    setTexts((current) => current.map((item) => (item.id === selectedTextId ? { ...item, text: value || " " } : item)));
  }

  function undoLast() {
    if (selectedTextId) {
      setTexts((current) => current.filter((item) => item.id !== selectedTextId));
      setSelectedTextId(null);
      return;
    }
    setPaths((current) => current.slice(0, -1));
  }

  function resetMarkup() {
    setPaths([]);
    setTexts([]);
    setSelectedTextId(null);
    clearEditedFile();
  }

  async function applyMarkup() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let blob: Blob | null = null;
    try {
      blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png", 0.95));
    } catch {
      setLoadError("当前图片跨域限制导致无法保存标注，请改用本地上传后再标注。");
      return;
    }
    if (!blob) return;
    const file = fileFromBlob(blob);
    const nextPreviewUrl = URL.createObjectURL(file);
    setMarkupPreviewUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return nextPreviewUrl;
    });
    onEditedFileChange(file, nextPreviewUrl);
    setOpen(false);
  }

  const hasSource = Boolean(sourceUrl);
  const modalContent = open ? (
    <div className="local-markup-modal-backdrop" role="presentation" onClick={() => setOpen(false)}>
      <div className="local-markup-modal-card" role="dialog" aria-modal="true" aria-label="局部修改标注" onClick={(event) => event.stopPropagation()}>
        <div className="local-markup-modal-header">
          <div className="local-markup-header-copy">
            <h3>局部修改标注</h3>
            <p>先标出需要重绘的位置，再在精修提示词里描述要怎么改。</p>
            <p>保存后会替换产品精修的第一张参考图；原始文件不会被覆盖。</p>
          </div>
          <div className="local-markup-toolbar" role="toolbar" aria-label="标注工具">
            <button type="button" className={tool === "mask" ? "active" : ""} onClick={() => setTool("mask")}>涂层</button>
            <button type="button" className={tool === "doodle" ? "active" : ""} onClick={() => setTool("doodle")}>涂鸦</button>
            <button type="button" className={tool === "text" ? "active" : ""} onClick={() => setTool("text")}>文字</button>
            <button type="button" className={tool === "move" ? "active" : ""} onClick={() => setTool("move")}>移动文字</button>
            <label>
              笔刷
              <input type="range" min={8} max={72} value={brushSize} onChange={(event) => setBrushSize(Number(event.target.value))} />
            </label>
            <button type="button" onClick={undoLast}>撤销</button>
            <button type="button" onClick={resetMarkup}>清空</button>
          </div>
          <div className="local-markup-header-actions">
            <button className="primary-button" type="button" onClick={applyMarkup} disabled={!imageReady}>
              保存为参考图
            </button>
            <button className="template-close-button" type="button" onClick={() => setOpen(false)} aria-label="关闭局部标注">
              ×
            </button>
          </div>
        </div>
        {selectedText ? (
          <label className="local-markup-text-editor">
            <span>文字内容</span>
            <input value={selectedText.text} onChange={(event) => updateSelectedText(event.target.value)} />
          </label>
        ) : null}
        <div className="local-markup-canvas-wrap">
          <canvas
            ref={canvasRef}
            className={imageReady ? "local-markup-canvas ready" : "local-markup-canvas"}
            style={{ cursor: tool === "mask" || tool === "doodle" ? "none" : tool === "text" ? "text" : "default" }}
            onPointerDown={handlePointerDown}
            onPointerEnter={updateBrushCursor}
            onPointerMove={handlePointerMove}
            onPointerLeave={hideBrushCursor}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          />
          {brushCursor.visible ? (
            <span
              className="local-markup-cursor"
              style={{
                width: brushCursor.size,
                height: brushCursor.size,
                transform: `translate(${brushCursor.x - brushCursor.size / 2}px, ${brushCursor.y - brushCursor.size / 2}px)`,
              }}
            />
          ) : null}
          {!imageReady ? <div className="local-markup-loading">{loadError ?? "正在载入参考图..."}</div> : null}
        </div>
      </div>
    </div>
  ) : null;

  return (
    <div className="local-markup-card">
      <div className="local-markup-head">
        <div>
          <h4>局部修改标注</h4>
          <p>涂层、涂鸦和文字会合成到参考图中，用于提示模型重点修改的位置。</p>
        </div>
        <button className="secondary-button compact-button" type="button" onClick={() => setOpen(true)} disabled={disabled || !hasSource}>
          打开局部标注
        </button>
      </div>
      <div className="local-markup-status">
        <span>{sourceName || "当前参考图"}</span>
        {markupPreviewUrl ? (
          <>
            <strong>已生成标注参考图，将替换第一张参考图</strong>
            <button type="button" onClick={clearEditedFile}>清除标注</button>
          </>
        ) : (
          <small>{hasSource ? "未标注，提交时使用原参考图。" : "请先上传或选择一张产品图。"}</small>
        )}
      </div>

      {modalContent ? (typeof document !== "undefined" ? createPortal(modalContent, document.body) : modalContent) : null}
    </div>
  );
}
