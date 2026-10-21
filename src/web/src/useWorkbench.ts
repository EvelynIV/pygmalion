import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Job } from "./api";
import { usePaperPreview } from "./usePaperPreview";

import {
  blank,
  newVector,
  type VectorElement,
  readDocument,
  type WritingDocument,
  type PaperElement,
  type Table,
  type Diagnostic,
} from "./canvas";
export type { WritingDocument } from "./canvas";
const storageKey = "pygmalion-document-v2";
function initialDocument() {
  try {
    const saved =
      localStorage.getItem(storageKey) ||
      localStorage.getItem("pygmalion-document-v1");
    return saved ? readDocument(JSON.parse(saved)) : structuredClone(blank);
  } catch {
    return structuredClone(blank);
  }
}
type Props = {
  busy: boolean;
  jobs?: Job[];
  notify: (text: string, error?: boolean) => void;
  refresh: () => void;
};
export function useWorkbench({ busy, jobs, notify, refresh }: Props) {
  const [document, setDocument] = useState(initialDocument);
  // 文件导出检查点与浏览器草稿分别追踪；自动保存不代表已经导出文件。
  const [saved, setSaved] = useState("");
  const [cached, setCached] = useState("");
  const [saveError, setSaveError] = useState<string>();
  const [inputEpoch, setInputEpoch] = useState(0);
  const [inputErrors, setInputErrors] = useState<Record<string, string>>({});
  const inputErrorsRef = useRef<Record<string, string>>({});
  const reportInput = useCallback((id: string, error?: string) => {
    if (inputErrorsRef.current[id] === error) return;
    const next = { ...inputErrorsRef.current };
    if (error) next[id] = error;
    else delete next[id];
    inputErrorsRef.current = next;
    setInputErrors(next);
  }, []);
  function resetInputs(restoreFocus = false) {
    const active = window.document.activeElement;
    const label =
      restoreFocus && active?.matches("input[data-number-field]")
        ? active.getAttribute("aria-label")
        : undefined;
    inputErrorsRef.current = {};
    setInputErrors({});
    setInputEpoch((v) => v + 1);
    if (label)
      requestAnimationFrame(() => {
        window.document
          .querySelector<HTMLInputElement>(
            `input[data-number-field][aria-label="${CSS.escape(label)}"]`,
          )
          ?.focus();
      });
  }
  function invalidInput() {
    const message = Object.values(inputErrorsRef.current)[0];
    if (message) notify(message, true);
    return !!message;
  }
  const latest = useRef(document);
  const cachedRef = useRef("");
  const history = useRef<{
    past: WritingDocument[];
    future: WritingDocument[];
    editing?: { key: string; baseline: WritingDocument };
  }>({ past: [], future: [] });
  const [historyState, setHistoryState] = useState({
    canUndo: false,
    canRedo: false,
  });
  function refreshHistory() {
    const state = history.current;
    setHistoryState({
      canUndo:
        !!state.past.length ||
        !!(
          state.editing &&
          JSON.stringify(state.editing.baseline) !==
            JSON.stringify(latest.current)
        ),
      canRedo: !!state.future.length,
    });
  }
  const [composing, setComposing] = useState(false);
  const persist = useCallback((next: WritingDocument) => {
    try {
      // 不能用无法重新打开的文档覆盖上一份可恢复草稿。
      readDocument(next);
      const snapshot = JSON.stringify(next);
      localStorage.setItem(storageKey, snapshot);
      cachedRef.current = snapshot;
      setCached(snapshot);
      setSaveError(undefined);
      return true;
    } catch (error) {
      setSaveError(
        "自动保存失败：" +
          (error instanceof Error ? error.message : "浏览器存储不可用"),
      );
      return false;
    }
  }, []);
  const migrationNotified = useRef(false);
  useEffect(() => {
    if (migrationNotified.current) return;
    migrationNotified.current = true;
    try {
      if (!localStorage.getItem(storageKey)) {
        const old = localStorage.getItem("pygmalion-document-v1");
        if (old && JSON.parse(old).direction === "vertical")
          notify("旧竖排文档已转换为横排，请检查纸面后保存");
      }
    } catch {
      /* 无效旧文档由加载器恢复为空白文档。 */
    }
  }, [notify]);
  useEffect(() => {
    persist(latest.current);
  }, [persist]);
  const [revision, setRevision] = useState(0);
  const [generation, setGeneration] = useState(0);
  const [writing, setWriting] = useState(false);
  const [jobId, setJobId] = useState<number>();
  const posting = useRef(false);
  const payload = JSON.stringify(document);
  const dirty = payload !== saved;
  const locked = busy || writing;
  const { preview, error, previewUrl } = usePaperPreview(
    document,
    generation,
    revision,
  );
  useEffect(() => {
    if (writing && jobId && jobs?.some((job) => job.job_id === jobId))
      queueMicrotask(() => setWriting(false));
  }, [jobs, jobId, writing]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (JSON.stringify(latest.current) !== cachedRef.current)
        event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  const lockedRef = useRef(locked);
  useEffect(() => {
    lockedRef.current = locked;
  }, [locked]);
  const [fitting, setFitting] = useState(false);
  function endEdit(key?: string) {
    const group = history.current.editing;
    if (!group || (key !== undefined && group.key !== key)) return;
    history.current.editing = undefined;
    if (JSON.stringify(group.baseline) !== JSON.stringify(latest.current)) {
      history.current.past = [
        ...history.current.past.slice(-49),
        group.baseline,
      ];
    }
    refreshHistory();
  }
  function beginEdit(key: string) {
    if (locked || lockedRef.current || history.current.editing?.key === key)
      return;
    endEdit();
    history.current.editing = { key, baseline: latest.current };
    refreshHistory();
  }
  function apply(next: WritingDocument) {
    // 不在 React updater/effect 中写存储：输入事件返回前就持久化最新正文。
    latest.current = next;
    persist(next);
    setDocument(next);
    refreshHistory();
  }
  function commit(next: WritingDocument) {
    if (locked || lockedRef.current) return;
    if (JSON.stringify(next) === JSON.stringify(latest.current)) {
      if (JSON.stringify(next) !== cachedRef.current) persist(next);
      return;
    }
    if (!history.current.editing)
      history.current.past = [
        ...history.current.past.slice(-49),
        latest.current,
      ];
    history.current.future = [];
    apply(next);
  }
  function replaceElement(element: PaperElement) {
    commit({
      ...latest.current,
      elements: latest.current.elements.map((e) =>
        e.id === element.id ? element : e,
      ),
    });
  }
  function replaceText(id: string, text: string, row?: number, col?: number) {
    const element = latest.current.elements.find((item) => item.id === id);
    if (!element || element.type === "vector") return;
    replaceElement(
      element.type === "text"
        ? { ...element, text }
        : {
            ...element,
            cells: element.cells.map((cell) =>
              cell.row === row && cell.col === col ? { ...cell, text } : cell,
            ),
          },
    );
  }
  function travel(direction: "undo" | "redo") {
    if (locked || lockedRef.current) return;
    resetInputs(true);
    const key = history.current.editing?.key;
    endEdit();
    const state = history.current;
    const next = direction === "undo" ? state.past.pop() : state.future.shift();
    if (next) {
      if (direction === "undo") state.future.unshift(latest.current);
      else state.past = [...state.past.slice(-49), latest.current];
      apply(next);
    }
    // 保持输入焦点；后续输入从恢复后的正文开始新的一次编辑。
    if (key !== undefined) state.editing = { key, baseline: latest.current };
    refreshHistory();
  }
  function undo() {
    travel("undo");
  }
  function redo() {
    travel("redo");
  }
  async function fit(id: string) {
    if (locked || fitting || invalidInput()) return;
    const snapshot = JSON.stringify(document);
    setFitting(true);
    try {
      const result = await api<{
        table: Table;
        diagnostics: Diagnostic[];
        success: boolean;
      }>("/api/workbench/table-fit", { document, element_id: id });
      if (
        lockedRef.current ||
        Object.keys(inputErrorsRef.current).length ||
        JSON.stringify(latest.current) !== snapshot
      ) {
        notify("文档已变化，请重新自适应", true);
        return;
      }
      if (!result.success) {
        notify(result.diagnostics.map((d) => d.message).join("；"), true);
        return;
      }
      replaceElement(result.table);
      notify("表格已自适应，外框尺寸保持不变");
    } catch (e) {
      notify(e instanceof Error ? e.message : "自适应失败", true);
    } finally {
      setFitting(false);
    }
  }
  function change<K extends keyof WritingDocument>(
    key: K,
    value: WritingDocument[K],
  ) {
    if (!locked) commit({ ...latest.current, [key]: value });
  }
  function newDocument() {
    if (!locked) {
      resetInputs();
      endEdit();
      setGeneration((value) => value + 1);
      commit(structuredClone(blank));
      setSaved("");
    }
  }
  function save() {
    if (invalidInput()) return;
    try {
      endEdit();
      const current = latest.current;
      const payload = JSON.stringify(current);
      readDocument(current);
      const cached = persist(current);
      const url = URL.createObjectURL(
        new Blob([payload], { type: "application/json" }),
      );
      const link = window.document.createElement("a");
      link.href = url;
      link.download =
        (current.name.replace(/[\\/:*?"<>|]/g, "_") || "未命名文档") +
        ".pygmalion.json";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setSaved(payload);
      notify(
        cached
          ? "文档已自动保存到本机，并导出文件"
          : "文件已导出；本机自动保存失败，请重试",
        !cached,
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "保存失败，请重试", true);
    }
  }
  async function open(input: File) {
    const baseline = latest.current;
    try {
      if (input.size > 4_000_000) throw new Error("文档文件过大");
      const text = await input.text();
      const next = input.name.toLowerCase().endsWith(".txt")
        ? readDocument({
            name: input.name.replace(/\.txt$/i, ""),
            paper: "A4",
            margin_mm: 20,
            body: text,
          })
        : readDocument(JSON.parse(text));
      if (next.elements.some((e) => e.type === "vector"))
        await api("/api/workbench/validate", next);
      if (latest.current !== baseline)
        throw new Error("文档已变化，请重新打开文件");
      if (lockedRef.current) {
        notify("书写中无法打开文档", true);
        return;
      }
      endEdit();
      setGeneration((value) => value + 1);
      resetInputs();
      commit(next);
      setSaved(JSON.stringify(next));
      notify(
        "文档已打开" +
          (!input.name.toLowerCase().endsWith(".txt") &&
          JSON.parse(text).direction === "vertical"
            ? "；旧竖排内容已转换为横排"
            : ""),
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "无法打开文档", true);
    }
  }
  const [importing, setImporting] = useState(false);
  const importPending = useRef(false);
  async function importSvg(input: File): Promise<string | undefined> {
    if (lockedRef.current || importPending.current) return;
    const baseline = latest.current;
    importPending.current = true;
    setImporting(true);
    try {
      if (input.size > 1_000_000) throw new Error("SVG 文件不能超过 1 MB");
      const result = await api<
        Pick<
          VectorElement,
          | "name"
          | "paths"
          | "width_mm"
          | "height_mm"
          | "fill_rules"
          | "draw_mode"
        >
      >("/api/workbench/import-svg", {
        source: await input.text(),
        name: input.name.replace(/\.svg$/i, "").slice(0, 120),
      });
      if (lockedRef.current || latest.current !== baseline)
        throw new Error("文档已变化，请重新导入 SVG");
      if (latest.current.elements.length >= 100)
        throw new Error("纸面最多支持 100 个元素");
      const vector = newVector(latest.current, result);
      const next = {
        ...latest.current,
        elements: [...latest.current.elements, vector],
      };
      readDocument(next);
      // 在改变文档前验证本机存储空间；失败时保留原文档。
      try {
        localStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        throw new Error(
          "SVG 导入失败：本机存储空间不足或不可用，请先导出当前文档",
        );
      }
      endEdit();
      commit(next);
      notify(
        result.draw_mode === "solid"
          ? "SVG 已导入：实心填充绘制"
          : "SVG 已导入：轮廓绘制",
      );
      return vector.id;
    } catch (error) {
      notify(error instanceof Error ? error.message : "SVG 导入失败", true);
    } finally {
      importPending.current = false;
      setImporting(false);
    }
  }
  const canWrite =
    !!document.elements.length &&
    !!preview?.can_print &&
    !preview.overflow &&
    !preview.missing.length &&
    !error &&
    !locked &&
    !fitting &&
    !Object.keys(inputErrors).length &&
    !composing;
  async function write() {
    if (!canWrite || posting.current || invalidInput()) return;
    posting.current = true;
    setWriting(true);
    try {
      const result = await api<{ job_id: number; dry_run: boolean }>(
        "/api/workbench/write",
        document,
      );
      setJobId(result.job_id);
      refresh();
      notify(
        result.dry_run
          ? "演练任务已开始，纸面与参数已冻结"
          : "书写任务已开始，纸面与参数已冻结",
      );
    } catch (e) {
      setWriting(false);
      notify(e instanceof Error ? e.message : "书写失败", true);
    } finally {
      posting.current = false;
    }
  }
  return {
    document,
    importSvg,
    importing,
    change,
    commit,
    replaceElement,
    undo,
    redo,
    canUndo: historyState.canUndo,
    canRedo: historyState.canRedo,
    beginEdit,
    endEdit,
    replaceText,
    composing,
    setComposing,
    saveError,
    inputEpoch,
    inputErrors,
    reportInput,
    locallySaved: cached === payload && !saveError,
    retrySave: () => persist(latest.current),
    fit,
    fitting,
    notify,
    locked,
    dirty,
    preview,
    previewUrl,
    error,
    canWrite,
    writing,
    jobId,
    newDocument,
    save,
    open,
    write,
    reload: () => {
      setRevision((v) => v + 1);
    },
  };
}
