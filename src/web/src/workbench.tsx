import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { PaperRulers } from "./PaperRulers";
import { NumberField, NumberFieldsProvider } from "./NumberField";
import { usePaperViewport } from "./usePaperViewport";
import { useResource, type Fonts } from "./api";
import { Button, Dialog, Link } from "./ui";
import { useWorkbench } from "./useWorkbench";
import {
  newText,
  newTable,
  pageSize,
  resizeElement,
  cellBox,
  mergeCells,
  splitCell,
  changeTrack,
  type PaperElement,
  type Table,
  type Cell,
} from "./canvas";

type WorkbenchModel = ReturnType<typeof useWorkbench>;
function InlineEditor({
  value,
  change,
  finish,
  begin,
  composing,
  label,
  size,
}: {
  value: string;
  change: (text: string) => void;
  finish: () => void;
  begin: () => void;
  composing: (active: boolean) => void;
  label: string;
  size: number;
}) {
  return (
    <textarea
      autoFocus
      className="paper-inline-editor"
      aria-label={label}
      value={value}
      spellCheck={false}
      style={{ fontSize: Math.max(12, size) }}
      onFocus={begin}
      onChange={(event) => change(event.target.value)}
      onCompositionStart={() => composing(true)}
      onCompositionEnd={(event) => {
        change(event.currentTarget.value);
        composing(false);
      }}
      onBlur={() => {
        composing(false);
        finish();
      }}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.keyCode === 229) return;
        event.stopPropagation();
        if (
          event.key === "Escape" ||
          (event.key === "Enter" && (event.ctrlKey || event.metaKey))
        ) {
          event.preventDefault();
          const container = event.currentTarget.parentElement;
          event.currentTarget.blur();
          if (container?.matches(".cell-hit-target")) {
            // 单元格容器不会卸载，立即恢复焦点以接住紧随 Esc 的方向键。
            container.focus();
          } else {
            requestAnimationFrame(() =>
              container
                ?.querySelector<HTMLElement>(".text-hit-target")
                ?.focus(),
            );
          }
        }
      }}
    />
  );
}
type WorkbenchProps = {
  model: WorkbenchModel;
  deviceText: string;
  port: string;
  busy: boolean;
};

export function Workbench(props: WorkbenchProps) {
  return (
    <NumberFieldsProvider report={props.model.reportInput}>
      <WorkbenchContent {...props} />
    </NumberFieldsProvider>
  );
}

function WorkbenchContent({
  model: m,
  deviceText,
  port,
  busy,
}: WorkbenchProps) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (
        event.isComposing ||
        event.keyCode === 229 ||
        m.composing ||
        event.altKey ||
        !(event.ctrlKey || event.metaKey) ||
        document.querySelector('dialog[open],[role="dialog"]')
      )
        return;
      const key = event.key.toLowerCase();
      if (key !== "z" && key !== "y") return;
      const target = event.target instanceof Element ? event.target : null;
      if (
        target?.closest('input,textarea,select,[contenteditable="true"]') &&
        !root.current?.contains(target)
      )
        return;
      event.preventDefault();
      event.stopPropagation();
      if (m.locked || pointer.current) return;
      if (key === "y" || event.shiftKey) m.redo();
      else m.undo();
    };
    document.addEventListener("keydown", shortcut, true);
    return () => document.removeEventListener("keydown", shortcut, true);
  }, [m]);
  const d = m.document;
  const [selected, setSelected] = useState<string>();
  const [extendSelection, setExtendSelection] = useState(false);
  const [cellSelection, setCellSelection] = useState<
    { row: number; col: number }[]
  >([]);
  const [editing, setEditing] = useState<{
    id: string;
    row?: number;
    col?: number;
  }>();
  const [discard, setDiscard] = useState<"new" | "open">();
  const [deletion, setDeletion] = useState<{
    table: Table;
    axis: "row" | "col";
    index: number;
  }>();
  const [inspectorOpen, setInspectorOpen] = useState(false);

  const [drag, setDrag] = useState<PaperElement>();
  const dragValue = useRef<PaperElement | undefined>(undefined);
  const pointer = useRef<
    | {
        x: number;
        y: number;
        element: PaperElement;
        mode: "move" | "resize" | "row" | "col";
        index?: number;
      }
    | undefined
  >(undefined);
  const file = useRef<HTMLInputElement>(null);
  const fonts = useResource<Fonts>("/api/fonts");
  const [width, height] = pageSize(d);
  const viewport = usePaperViewport(width, height);
  const { stage, paper, zoom, setZoom, scale } = viewport;
  const element = d.elements.find((e) => e.id === selected);
  const table = element?.type === "table" ? element : undefined;
  const chosenCells =
    table?.cells.filter((c) =>
      cellSelection.some((s) => s.row === c.row && s.col === c.col),
    ) || [];
  const activeCell = chosenCells[0];
  const errors = m.preview?.diagnostics || [];
  function select(id?: string) {
    m.endEdit();
    m.setComposing(false);
    setSelected(id);
    setInspectorOpen(!!id);
    setExtendSelection(false);
    setCellSelection([]);
    setEditing(undefined);
  }
  function safe(action: () => void) {
    try {
      action();
    } catch (e) {
      m.notify(e instanceof Error ? e.message : "操作失败", true);
    }
  }
  function request(action: "new" | "open") {
    if (m.locked) return;
    if (m.dirty && (d.elements.length || d.name !== "未命名文档"))
      setDiscard(action);
    else perform(action);
  }
  function perform(action: "new" | "open") {
    select();
    if (action === "new") m.newDocument();
    else file.current?.click();
  }
  function patch(changes: Partial<PaperElement>) {
    if (element) m.replaceElement({ ...element, ...changes } as PaperElement);
  }
  function updateCell(changes: Partial<Cell>) {
    if (table && activeCell)
      m.replaceElement({
        ...table,
        cells: table.cells.map((c) =>
          chosenCells.includes(c) ? { ...c, ...changes } : c,
        ),
      });
  }
  function chooseCell(t: Table, c: Cell, range: boolean) {
    m.endEdit();
    m.setComposing(false);
    setEditing(undefined);
    setSelected(t.id);
    setInspectorOpen(true);
    if (range && selected === t.id && activeCell) {
      const r0 = Math.min(activeCell.row, c.row),
        c0 = Math.min(activeCell.col, c.col),
        r1 = Math.max(activeCell.row + activeCell.rowspan, c.row + c.rowspan),
        c1 = Math.max(activeCell.col + activeCell.colspan, c.col + c.colspan);
      setCellSelection(
        t.cells
          .filter(
            (v) =>
              v.row < r1 &&
              v.row + v.rowspan > r0 &&
              v.col < c1 &&
              v.col + v.colspan > c0,
          )
          .map((v) => ({ row: v.row, col: v.col })),
      );
    } else setCellSelection([{ row: c.row, col: c.col }]);
  }
  function finishText(key: string) {
    m.endEdit(key);
    setEditing(undefined);
  }
  function begin(
    e: ReactPointerEvent<HTMLButtonElement>,
    element: PaperElement,
    mode: "move" | "resize" | "row" | "col",
    index?: number,
  ) {
    if (m.locked || e.button !== 0) return;
    m.endEdit();
    m.setComposing(false);
    e.preventDefault();
    e.stopPropagation();
    setSelected(element.id);
    setEditing(undefined);
    e.currentTarget.setPointerCapture(e.pointerId);
    pointer.current = { x: e.clientX, y: e.clientY, element, mode, index };
    dragValue.current = element;
    setDrag(element);
  }
  function move(e: ReactPointerEvent<HTMLButtonElement>) {
    const p = pointer.current;
    if (!p) return;
    const dx = (e.clientX - p.x) / scale,
      dy = (e.clientY - p.y) / scale,
      el = p.element;
    let next = el;
    if (p.mode === "move")
      next = {
        ...el,
        x_mm: Math.max(
          d.margin_mm,
          Math.min(width - d.margin_mm - el.width_mm, el.x_mm + dx),
        ),
        y_mm: Math.max(
          d.margin_mm,
          Math.min(height - d.margin_mm - el.height_mm, el.y_mm + dy),
        ),
      };
    else if (p.mode === "resize")
      next = resizeElement(
        el,
        Math.max(3, Math.min(width - d.margin_mm - el.x_mm, el.width_mm + dx)),
        Math.max(
          3,
          Math.min(height - d.margin_mm - el.y_mm, el.height_mm + dy),
        ),
      );
    else if (el.type === "table") {
      const key = p.mode === "row" ? "row_heights" : "column_widths",
        i = p.index!,
        sizes = [...el[key]];
      const delta = Math.max(
        0.5 - sizes[i],
        Math.min(sizes[i + 1] - 0.5, p.mode === "row" ? dy : dx),
      );
      sizes[i] += delta;
      sizes[i + 1] -= delta;
      next = { ...el, [key]: sizes };
    }
    dragValue.current = next;
    setDrag(next);
  }
  function finish(e: ReactPointerEvent<HTMLButtonElement>) {
    if (!pointer.current) return;
    if (
      !m.locked &&
      dragValue.current &&
      JSON.stringify(dragValue.current) !==
        JSON.stringify(pointer.current.element)
    )
      m.replaceElement(dragValue.current);
    pointer.current = undefined;
    dragValue.current = undefined;
    setDrag(undefined);
    if (e.currentTarget.hasPointerCapture(e.pointerId))
      e.currentTarget.releasePointerCapture(e.pointerId);
  }
  const dragProps = {
    onPointerMove: move,
    onPointerUp: finish,
    onPointerCancel: () => {
      pointer.current = undefined;
      dragValue.current = undefined;
      setDrag(undefined);
    },
  };
  function track(axis: "row" | "col", remove: boolean) {
    if (!table) return;
    const index = activeCell?.[axis] ?? 0;
    if (
      remove &&
      table.cells.some(
        (c) =>
          c[axis] <= index &&
          c[axis] + c[axis === "row" ? "rowspan" : "colspan"] > index &&
          c.text.trim(),
      )
    )
      setDeletion({ table, axis, index });
    else
      safe(() => {
        m.replaceElement(changeTrack(table, axis, index, remove));
        setCellSelection([]);
      });
  }
  function setTrack(axis: "row" | "col", index: number, value: number) {
    if (!table) return;
    const key = axis === "row" ? "row_heights" : "column_widths",
      sizes = [...table[key]],
      other = index === sizes.length - 1 ? index - 1 : index + 1;
    if (other < 0) return;
    const total = sizes[index] + sizes[other];
    if (value < 0.25 || value > total - 0.25) return;
    sizes[index] = value;
    sizes[other] = total - value;
    m.replaceElement({ ...table, [key]: sizes });
  }
  const feedback = m.error
    ? "预览失败：" + m.error + (m.previewUrl ? "；当前显示上次成功预览" : "")
    : !d.elements.length
      ? "添加文本框或表格，开始编排纸面"
      : !m.preview
        ? m.previewUrl
          ? "正在更新笔画预览…"
          : "正在生成笔画预览…"
        : errors.length
          ? errors
              .map((v) => v.message)
              .filter((v, i, a) => a.indexOf(v) === i)
              .join("；")
          : !m.preview.can_print
            ? "没有可绘制的内容"
            : busy
              ? "当前纸面与机器参数已冻结"
              : "● 内容位于可书写区域内";
  return (
    <div
      ref={root}
      className="single-page-workbench canvas-workbench"
      onFocusCapture={(event) => {
        const target = event.target;
        if (
          target instanceof HTMLInputElement &&
          (target.type === "text" || target.type === "number")
        )
          m.beginEdit(target.getAttribute("aria-label") || target.name);
      }}
      onBlurCapture={(event) => {
        const target = event.target;
        if (
          target instanceof HTMLInputElement &&
          (target.type === "text" || target.type === "number")
        )
          m.endEdit(target.getAttribute("aria-label") || target.name);
      }}
    >
      <div className="document-toolbar">
        <input
          className="document-name"
          aria-label="文档名称"
          value={d.name}
          maxLength={120}
          disabled={m.locked}
          onChange={(e) => m.change("name", e.target.value)}
        />
        <span
          className="document-save-status muted"
          aria-live="polite"
          title={m.saveError}
        >
          {Object.keys(m.inputErrors).length
            ? "有尚未生效的数值，请修正后保存或书写"
            : m.saveError
              ? "自动保存失败，文字暂留本页"
              : m.locallySaved
                ? "已自动保存到本机"
                : "尚未自动保存"}
          {m.saveError && (
            <Button kind="ghost" onClick={m.retrySave}>
              重试保存
            </Button>
          )}
        </span>
        <div className="document-actions">
          <Button
            kind="ghost"
            disabled={m.locked}
            onClick={() => request("new")}
          >
            新建
          </Button>
          <Button
            kind="ghost"
            disabled={m.locked}
            onClick={() => request("open")}
          >
            打开
          </Button>
          <Button
            kind="ghost"
            disabled={m.locked || !!Object.keys(m.inputErrors).length}
            onClick={m.save}
          >
            保存
          </Button>
        </div>
        <Link className="quick-device muted" href="/admin/machine">
          ● {deviceText} · {port === "auto" ? "自动串口" : port}
        </Link>
      </div>
      <div className="canvas-tools" role="toolbar" aria-label="纸面工具">
        <Button
          disabled={m.locked || d.elements.length >= 100}
          onClick={() => {
            const e = newText(d);
            m.change("elements", [...d.elements, e]);
            select(e.id);
            setEditing({ id: e.id });
          }}
        >
          添加文本框
        </Button>
        <Button
          disabled={m.locked || d.elements.length >= 100}
          onClick={() => {
            const e = newTable(d);
            m.change("elements", [...d.elements, e]);
            select(e.id);
          }}
        >
          添加表格
        </Button>
        <Button
          className="inspector-toggle"
          kind="ghost"
          aria-expanded={inspectorOpen}
          aria-controls="paper-inspector"
          onClick={() => setInspectorOpen((v) => !v)}
        >
          纸面与属性
        </Button>
        <Button
          kind="ghost"
          aria-label="小手工具 (H)"
          aria-pressed={viewport.hand}
          onClick={() => viewport.setHand(!viewport.hand)}
        >
          小手 H
        </Button>
        <span className="tool-divider" />
        <Button
          kind="ghost"
          disabled={m.locked || !m.canUndo}
          onClick={() => {
            setEditing(undefined);
            m.undo();
          }}
        >
          撤销
        </Button>
        <Button
          kind="ghost"
          disabled={m.locked || !m.canRedo}
          onClick={() => {
            setEditing(undefined);
            m.redo();
          }}
        >
          重做
        </Button>
        <label className="canvas-zoom">
          视图{" "}
          <select
            aria-label="纸面缩放"
            value={zoom}
            onChange={(e) => setZoom(e.target.value)}
          >
            <option value="fit">适合窗口</option>
            {!["fit", "0.5", "1", "1.5", "2", "3", "4"].includes(zoom) && (
              <option value={zoom}>{Math.round(Number(zoom) * 100)}%</option>
            )}
            <option value="0.5">50%</option>
            <option value="1">100%</option>
            <option value="1.5">150%</option>
            <option value="2">200%</option>
            <option value="3">300%</option>
            <option value="4">400%</option>
          </select>
        </label>
        <span className="muted">
          {d.paper} · {width} × {height} mm
        </span>
      </div>
      <div
        className={"canvas-columns " + (inspectorOpen ? "inspector-open" : "")}
      >
        <section
          className="page-preview"
          aria-label="纸面编辑器"
          onKeyDown={(e) => {
            if (
              m.locked ||
              editing ||
              (e.target instanceof Element &&
                e.target.closest("input,textarea,select"))
            )
              return;
            if (e.key === "Escape") select();
            if (element && (e.key === "Delete" || e.key === "Backspace")) {
              e.preventDefault();
              m.change(
                "elements",
                d.elements.filter((v) => v.id !== element.id),
              );
              select();
            }
          }}
        >
          <div className="paper-viewport">
            <PaperRulers
              width={width}
              height={height}
              scale={scale}
              view={viewport.view}
              cursor={viewport.cursor}
            />
            <div
              className={
                "single-paper-stage canvas-stage" +
                (viewport.hand ? " hand-tool" : "") +
                (viewport.panning ? " is-panning" : "")
              }
              ref={stage}
              onPointerMove={(e) => viewport.point(e.clientX, e.clientY)}
              onPointerLeave={viewport.clearCursor}
              onClick={(e) => {
                if (e.target === e.currentTarget) select();
              }}
            >
              <div
                ref={paper}
                className={
                  "single-paper canvas-paper " +
                  (errors.length ? "invalid" : "")
                }
                style={{
                  width: width * scale,
                  height: height * scale,
                  transform: `translate(${viewport.pan.x}px, ${viewport.pan.y}px)`,
                }}
                onClick={(e) => {
                  if (e.target === e.currentTarget) select();
                }}
              >
                {m.previewUrl ? (
                  <img
                    src={m.previewUrl}
                    alt="纸面真实笔画预览"
                    draggable={false}
                  />
                ) : (
                  <div
                    className="empty-margin-guide"
                    style={{
                      inset: `${(100 * d.margin_mm) / height}% ${(100 * d.margin_mm) / width}%`,
                    }}
                  />
                )}
                {d.elements.map((original) => {
                  const el = drag?.id === original.id ? drag : original,
                    isSelected = selected === el.id,
                    invalid = errors.some((v) => v.element_id === el.id);
                  const isEditing = !m.locked && editing?.id === el.id;
                  return (
                    <div
                      key={el.id}
                      className={
                        "paper-element " +
                        (isSelected ? "selected " : "") +
                        (invalid ? "element-invalid" : "")
                      }
                      data-element-id={el.id}
                      style={{
                        left: el.x_mm * scale,
                        top: el.y_mm * scale,
                        width: el.width_mm * scale,
                        height: el.height_mm * scale,
                      }}
                    >
                      {el.type === "text" ? (
                        isEditing ? (
                          <InlineEditor
                            key={el.id}
                            label="文本框内容"
                            value={el.text}
                            size={el.font_size_mm * scale}
                            begin={() => m.beginEdit(el.id)}
                            finish={() => finishText(el.id)}
                            composing={m.setComposing}
                            change={(text) => m.replaceText(el.id, text)}
                          />
                        ) : (
                          <button
                            className="text-hit-target"
                            disabled={m.locked}
                            aria-label={"文本框：" + (el.text || "空白")}
                            onClick={() => {
                              select(el.id);
                              setEditing({ id: el.id });
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                select(el.id);
                                setEditing({ id: el.id });
                              }
                            }}
                          >
                            {!el.text && <span>点击输入文字</span>}
                          </button>
                        )
                      ) : (
                        <div
                          className="paper-table"
                          role="grid"
                          aria-label="表格"
                          aria-rowcount={el.row_heights.length}
                          aria-colcount={el.column_widths.length}
                        >
                          {el.cells.map((c) => {
                            const b = cellBox(el, c),
                              selectedCell =
                                isSelected &&
                                cellSelection.some(
                                  (s) => s.row === c.row && s.col === c.col,
                                ),
                              editCell =
                                isEditing &&
                                editing?.row === c.row &&
                                editing?.col === c.col;
                            return (
                              <div
                                className={
                                  "paper-cell " +
                                  (selectedCell ? "cell-selected " : "") +
                                  (errors.some(
                                    (v) =>
                                      v.element_id === el.id &&
                                      v.row === c.row &&
                                      v.col === c.col,
                                  )
                                    ? "cell-invalid"
                                    : "")
                                }
                                role="row"
                                key={`${c.row}:${c.col}`}
                                style={{
                                  left: b.x * scale,
                                  top: b.y * scale,
                                  width: b.width * scale,
                                  height: b.height * scale,
                                }}
                              >
                                <div
                                  role="gridcell"
                                  className="cell-hit-target"
                                  aria-rowindex={c.row + 1}
                                  aria-colindex={c.col + 1}
                                  aria-rowspan={c.rowspan}
                                  aria-colspan={c.colspan}
                                  aria-selected={selectedCell}
                                  aria-label={`单元格 ${c.row + 1}行${c.col + 1}列${c.text ? "：" + c.text : ""}`}
                                  aria-disabled={m.locked}
                                  tabIndex={m.locked ? -1 : 0}
                                  onClick={(event) => {
                                    if (m.locked || editCell) return;
                                    const range =
                                      event.shiftKey || extendSelection;
                                    chooseCell(el, c, range);
                                    if (range) event.currentTarget.focus();
                                    else
                                      setEditing({
                                        id: el.id,
                                        row: c.row,
                                        col: c.col,
                                      });
                                  }}
                                  onKeyDown={(e) => {
                                    if (
                                      m.locked ||
                                      editCell ||
                                      e.nativeEvent.isComposing ||
                                      e.keyCode === 229
                                    )
                                      return;
                                    if (e.key === "Enter") {
                                      e.preventDefault();
                                      chooseCell(el, c, false);
                                      setEditing({
                                        id: el.id,
                                        row: c.row,
                                        col: c.col,
                                      });
                                    }
                                    const delta: Record<
                                      string,
                                      [number, number]
                                    > = {
                                      ArrowUp: [-1, 0],
                                      ArrowDown: [c.rowspan, 0],
                                      ArrowLeft: [0, -1],
                                      ArrowRight: [0, c.colspan],
                                    };
                                    if (delta[e.key]) {
                                      e.preventDefault();
                                      const [dr, dc] = delta[e.key],
                                        target = el.cells.find(
                                          (v) =>
                                            c.row + dr >= v.row &&
                                            c.row + dr < v.row + v.rowspan &&
                                            c.col + dc >= v.col &&
                                            c.col + dc < v.col + v.colspan,
                                        );
                                      if (target) {
                                        chooseCell(el, target, e.shiftKey);
                                        const root =
                                          e.currentTarget.closest(
                                            ".paper-table",
                                          );
                                        (
                                          root?.querySelector(
                                            `[aria-rowindex="${target.row + 1}"][aria-colindex="${target.col + 1}"]`,
                                          ) as HTMLElement
                                        )?.focus();
                                      }
                                    }
                                  }}
                                >
                                  {editCell && (
                                    <InlineEditor
                                      key={`${el.id}:${c.row}:${c.col}`}
                                      label={`单元格 ${c.row + 1}行${c.col + 1}列内容`}
                                      value={c.text}
                                      size={el.font_size_mm * scale}
                                      begin={() =>
                                        m.beginEdit(
                                          `${el.id}:${c.row}:${c.col}`,
                                        )
                                      }
                                      finish={() =>
                                        finishText(`${el.id}:${c.row}:${c.col}`)
                                      }
                                      composing={m.setComposing}
                                      change={(text) =>
                                        m.replaceText(el.id, text, c.row, c.col)
                                      }
                                    />
                                  )}
                                </div>
                              </div>
                            );
                          })}
                          {isSelected &&
                            !m.locked &&
                            (["row", "col"] as const).flatMap((axis) => {
                              const sizes =
                                axis === "row"
                                  ? el.row_heights
                                  : el.column_widths;
                              return sizes.slice(0, -1).map((_, i) => {
                                const pos =
                                  sizes
                                    .slice(0, i + 1)
                                    .reduce((a, b) => a + b, 0) * scale;
                                return (
                                  <button
                                    key={axis + i}
                                    className={"track-handle " + axis}
                                    aria-label={`调整第${i + 1}${axis === "row" ? "行高" : "列宽"}`}
                                    style={
                                      axis === "row"
                                        ? { top: pos }
                                        : { left: pos }
                                    }
                                    onPointerDown={(e) =>
                                      begin(e, original, axis, i)
                                    }
                                    {...dragProps}
                                  />
                                );
                              });
                            })}
                        </div>
                      )}
                      {isSelected && (
                        <>
                          <button
                            className="element-grip"
                            aria-label={
                              el.type === "text" ? "移动文本框" : "移动表格"
                            }
                            disabled={m.locked}
                            onPointerDown={(e) => begin(e, original, "move")}
                            {...dragProps}
                            onKeyDown={(e) => {
                              const delta: Record<string, [number, number]> = {
                                ArrowUp: [0, -1],
                                ArrowDown: [0, 1],
                                ArrowLeft: [-1, 0],
                                ArrowRight: [1, 0],
                              };
                              if (delta[e.key]) {
                                e.preventDefault();
                                e.stopPropagation();
                                const [dx, dy] = delta[e.key],
                                  step = e.shiftKey ? 1 : 0.1;
                                patch({
                                  x_mm: el.x_mm + dx * step,
                                  y_mm: el.y_mm + dy * step,
                                });
                              }
                            }}
                          >
                            ⠿ {el.type === "text" ? "文本框" : "表格"}
                          </button>
                          <button
                            className="element-resize"
                            aria-label="调整元素大小"
                            disabled={m.locked}
                            onPointerDown={(e) => begin(e, original, "resize")}
                            {...dragProps}
                          />
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
          <div
            className={
              "inline-preview-status " +
              (m.error || errors.length ? "invalid" : "")
            }
            role={m.error || errors.length ? "alert" : "status"}
          >
            <p>{feedback}</p>
            <p className="paper-coordinate-readout" aria-label="纸面坐标">
              {viewport.cursor
                ? `X ${viewport.cursor.x.toFixed(2)} mm · Y ${viewport.cursor.y.toFixed(2)} mm`
                : "纸张左上角为 0, 0 · 单位 mm"}{" "}
              · Ctrl / ⌘ + 滚轮缩放纸面 · H 小手平移 / Esc 选择
            </p>
            <p className="muted">
              单击输入；Shift 点击选择多个单元格；虚线和选择框不落笔。
            </p>
          </div>
        </section>
        <aside
          id="paper-inspector"
          className="single-page-inspector canvas-inspector"
          aria-label="纸面与元素设置"
        >
          <details open={!element}>
            <summary>纸面设置</summary>
            <fieldset key={m.inputEpoch} disabled={m.locked}>
              <label className="compact-field">
                <span>纸张</span>
                <select
                  aria-label="纸张"
                  value={d.paper}
                  onChange={(e) =>
                    m.change("paper", e.target.value as "A4" | "A5")
                  }
                >
                  <option value="A4">A4 · 210 × 297 mm</option>
                  <option value="A5">A5 · 148 × 210 mm</option>
                </select>
              </label>
              <NumberField
                label="统一页边距"
                value={d.margin_mm}
                max={60}
                change={(v) => m.change("margin_mm", v)}
              />
            </fieldset>
          </details>
          {element ? (
            <>
              <h2>{element.type === "text" ? "文本框设置" : "表格设置"}</h2>
              <fieldset
                key={`${m.inputEpoch}:${element.id}:${activeCell?.row}:${activeCell?.col}`}
                disabled={m.locked}
              >
                <div className="dimension-grid">
                  {(
                    [
                      ["x_mm", "横向位置"],
                      ["y_mm", "纵向位置"],
                      ["width_mm", "宽度"],
                      ["height_mm", "高度"],
                    ] as const
                  ).map(([key, label]) => (
                    <NumberField
                      key={key}
                      label={label}
                      value={element[key]}
                      min={key === "width_mm" || key === "height_mm" ? 0.5 : 0}
                      change={(v) =>
                        key === "width_mm" || key === "height_mm"
                          ? m.replaceElement(
                              resizeElement(
                                element,
                                key === "width_mm" ? v : element.width_mm,
                                key === "height_mm" ? v : element.height_mm,
                              ),
                            )
                          : patch({ [key]: v })
                      }
                    />
                  ))}
                </div>
                <label className="compact-field">
                  <span>字体</span>
                  <select
                    aria-label="字体"
                    value={element.font}
                    onChange={(e) => patch({ font: e.target.value })}
                  >
                    <option value="">数据集默认字体</option>
                    {fonts.data?.fonts.map((f) => (
                      <option key={f.id} value={f.id} disabled={!f.available}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                  {fonts.error && (
                    <small>
                      字体列表读取失败{" "}
                      <Button kind="ghost" onClick={fonts.reload}>
                        重试
                      </Button>
                    </small>
                  )}
                </label>
                <div className="dimension-grid">
                  {(
                    [
                      ["font_size_mm", "字号", 4, 40],
                      ["char_gap_mm", "字间距", 0, 20],
                      ["line_gap_mm", "行间距", 0, 30],
                    ] as const
                  ).map(([key, label, min, max]) => (
                    <NumberField
                      key={key}
                      label={label}
                      value={element[key]}
                      min={min}
                      max={max}
                      change={(v) => patch({ [key]: v })}
                    />
                  ))}
                </div>
                {element.type === "text" && (
                  <label className="compact-field">
                    <span>对齐</span>
                    <select
                      aria-label="文本对齐"
                      value={element.align}
                      onChange={(e) =>
                        patch({
                          align: e.target.value as "left" | "center" | "right",
                        })
                      }
                    >
                      <option value="left">左对齐</option>
                      <option value="center">居中</option>
                      <option value="right">右对齐</option>
                    </select>
                  </label>
                )}
                {table && (
                  <>
                    <label className="canvas-checkbox">
                      <input
                        type="checkbox"
                        checked={table.draw_borders}
                        onChange={(e) =>
                          patch({ draw_borders: e.target.checked })
                        }
                      />
                      实际书写表格线
                    </label>
                    <NumberField
                      label="单元格内边距"
                      value={table.padding_mm}
                      max={10}
                      change={(v) => patch({ padding_mm: v })}
                    />
                    <Button
                      disabled={
                        m.fitting || !!Object.keys(m.inputErrors).length
                      }
                      onClick={() => void m.fit(table.id)}
                    >
                      {m.fitting ? "正在计算…" : "自适应"}
                    </Button>
                    <p className="muted">
                      固定外框，优先保持原布局和字号；放不下时调整行列，必要时缩小字号，最低
                      4 mm。不会放大字号；仅点击时执行。
                    </p>
                    <Button
                      kind="ghost"
                      aria-pressed={extendSelection}
                      disabled={!activeCell}
                      onClick={() => setExtendSelection((v) => !v)}
                    >
                      {extendSelection ? "结束扩展选区" : "扩展单元格选区"}
                    </Button>
                    <div className="table-actions">
                      <Button onClick={() => track("row", false)}>
                        插入行
                      </Button>
                      <Button onClick={() => track("col", false)}>
                        插入列
                      </Button>
                      <Button
                        disabled={table.row_heights.length === 1}
                        onClick={() => track("row", true)}
                      >
                        删除行
                      </Button>
                      <Button
                        disabled={table.column_widths.length === 1}
                        onClick={() => track("col", true)}
                      >
                        删除列
                      </Button>
                    </div>
                    {activeCell && (
                      <>
                        <p className="muted">
                          已选 {chosenCells.length} 格 · 第 {activeCell.row + 1}{" "}
                          行，第 {activeCell.col + 1} 列
                        </p>
                        <div className="table-actions">
                          <Button
                            disabled={chosenCells.length < 2}
                            onClick={() =>
                              safe(() => {
                                const next = mergeCells(table, chosenCells);
                                m.replaceElement(next);
                                setCellSelection([
                                  {
                                    row: Math.min(
                                      ...chosenCells.map((c) => c.row),
                                    ),
                                    col: Math.min(
                                      ...chosenCells.map((c) => c.col),
                                    ),
                                  },
                                ]);
                              })
                            }
                          >
                            合并单元格
                          </Button>
                          <Button
                            disabled={
                              chosenCells.length !== 1 ||
                              (activeCell.rowspan === 1 &&
                                activeCell.colspan === 1)
                            }
                            onClick={() =>
                              m.replaceElement(splitCell(table, activeCell))
                            }
                          >
                            拆分单元格
                          </Button>
                        </div>
                        <div className="dimension-grid">
                          <NumberField
                            label="当前行高"
                            value={table.row_heights[activeCell.row]}
                            min={0.25}
                            max={
                              table.row_heights.length > 1
                                ? table.row_heights[activeCell.row] +
                                  table.row_heights[
                                    activeCell.row ===
                                    table.row_heights.length - 1
                                      ? activeCell.row - 1
                                      : activeCell.row + 1
                                  ] -
                                  0.25
                                : table.height_mm
                            }
                            disabled={table.row_heights.length === 1}
                            change={(v) => setTrack("row", activeCell.row, v)}
                          />
                          <NumberField
                            label="当前列宽"
                            value={table.column_widths[activeCell.col]}
                            min={0.25}
                            max={
                              table.column_widths.length > 1
                                ? table.column_widths[activeCell.col] +
                                  table.column_widths[
                                    activeCell.col ===
                                    table.column_widths.length - 1
                                      ? activeCell.col - 1
                                      : activeCell.col + 1
                                  ] -
                                  0.25
                                : table.width_mm
                            }
                            disabled={table.column_widths.length === 1}
                            change={(v) => setTrack("col", activeCell.col, v)}
                          />
                        </div>
                        <label className="compact-field">
                          <span>单元格水平对齐</span>
                          <select
                            aria-label="单元格水平对齐"
                            value={activeCell.align}
                            onChange={(e) =>
                              updateCell({
                                align: e.target.value as Cell["align"],
                              })
                            }
                          >
                            <option value="left">左对齐</option>
                            <option value="center">居中</option>
                            <option value="right">右对齐</option>
                          </select>
                        </label>
                        <label className="compact-field">
                          <span>单元格垂直对齐</span>
                          <select
                            aria-label="单元格垂直对齐"
                            value={activeCell.valign}
                            onChange={(e) =>
                              updateCell({
                                valign: e.target.value as Cell["valign"],
                              })
                            }
                          >
                            <option value="top">顶部</option>
                            <option value="center">居中</option>
                            <option value="bottom">底部</option>
                          </select>
                        </label>
                      </>
                    )}
                  </>
                )}
                <div className="table-actions">
                  <Button
                    kind="ghost"
                    onClick={() => {
                      if (element.type === "text")
                        setEditing({ id: element.id });
                      else if (activeCell)
                        setEditing({
                          id: element.id,
                          row: activeCell.row,
                          col: activeCell.col,
                        });
                    }}
                    disabled={element.type === "table" && !activeCell}
                  >
                    编辑内容
                  </Button>
                  <Button
                    kind="danger"
                    onClick={() => {
                      m.change(
                        "elements",
                        d.elements.filter((e) => e.id !== element.id),
                      );
                      select();
                    }}
                  >
                    删除元素
                  </Button>
                </div>
              </fieldset>
            </>
          ) : (
            <p className="muted inspector-empty">
              选择纸面上的文本框或表格，调整位置、尺寸和文字样式。
            </p>
          )}
          <Button kind="ghost" onClick={m.reload}>
            重新预览
          </Button>
        </aside>
      </div>
      <input
        type="file"
        hidden
        ref={file}
        accept=".json,.txt"
        onChange={(e) => {
          const input = e.target.files?.[0];
          if (input) {
            select();
            void m.open(input);
          }
          e.target.value = "";
        }}
      />
      {discard && (
        <Dialog
          title={discard === "new" ? "新建空白文档？" : "打开其他文档？"}
          confirmLabel={discard === "new" ? "继续新建" : "继续打开"}
          close={() => setDiscard(undefined)}
          confirm={async () => perform(discard)}
        >
          当前文档尚未导出文件。继续操作将替换本机草稿，请先保存文件以保留副本。
        </Dialog>
      )}
      {deletion && (
        <Dialog
          title="删除含文字的行列？"
          confirmLabel="确认删除"
          close={() => setDeletion(undefined)}
          confirm={async () =>
            safe(() => {
              m.replaceElement(
                changeTrack(
                  deletion.table,
                  deletion.axis,
                  deletion.index,
                  true,
                ),
              );
              setCellSelection([]);
            })
          }
        >
          所选行列包含文字。删除后可使用撤销恢复。
        </Dialog>
      )}
    </div>
  );
}
