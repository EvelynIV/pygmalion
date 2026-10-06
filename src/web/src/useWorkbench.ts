import { useEffect, useRef, useState } from "react";
import { api, type Job, type Preview } from "./api";

export interface WritingDocument {
  name: string;
  body: string;
  font: string;
  paper: "A4" | "A5";
  direction: "horizontal" | "vertical";
  align: "left" | "center" | "right";
  font_size_mm: number;
  char_gap_mm: number;
  line_gap_mm: number;
  margin_mm: number;
}
const blank: WritingDocument = {
  name: "未命名文档",
  body: "",
  font: "",
  paper: "A4",
  direction: "horizontal",
  align: "left",
  font_size_mm: 8,
  char_gap_mm: 0,
  line_gap_mm: 3,
  margin_mm: 20,
};
const storageKey = "pygmalion-document-v1";
function readDocument(raw: unknown): WritingDocument {
  if (!raw || typeof raw !== "object") throw new Error("文档格式不正确");
  const d = raw as WritingDocument;
  if (
    typeof d.name !== "string" ||
    d.name.length > 120 ||
    typeof d.body !== "string" ||
    d.body.length > 20000 ||
    typeof d.font !== "string" ||
    d.font.length > 120 ||
    !["A4", "A5"].includes(d.paper) ||
    !["horizontal", "vertical"].includes(d.direction) ||
    !["left", "center", "right"].includes(d.align)
  )
    throw new Error("文档内容或排版字段不正确");
  for (const [key, min, max] of [
    ["font_size_mm", 4, 40],
    ["char_gap_mm", 0, 20],
    ["line_gap_mm", 0, 30],
    ["margin_mm", 0, 60],
  ] as const)
    if (!Number.isFinite(d[key]) || d[key] < min || d[key] > max)
      throw new Error("文档中的排版数值无效");
  return Object.fromEntries(
    Object.keys(blank).map((key) => [key, d[key as keyof WritingDocument]]),
  ) as unknown as WritingDocument;
}
function initialDocument() {
  try {
    const saved = localStorage.getItem(storageKey);
    return saved ? readDocument(JSON.parse(saved)) : { ...blank };
  } catch {
    return { ...blank };
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
  const [saved, setSaved] = useState(() => {
    try {
      return localStorage.getItem(storageKey) || "";
    } catch {
      return "";
    }
  });
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{
    key: string;
    data?: Preview & { overflow: boolean };
    error?: string;
  }>();
  const [writing, setWriting] = useState(false);
  const [jobId, setJobId] = useState<number>();
  const posting = useRef(false);
  const payload = JSON.stringify(document);
  const dirty = payload !== saved;
  const locked = busy || writing;
  const preview = result?.key === payload ? result.data : undefined;
  const error = result?.key === payload ? result.error : undefined;
  useEffect(() => {
    if (writing && jobId && jobs?.some((job) => job.job_id === jobId))
      queueMicrotask(() => setWriting(false));
  }, [jobs, jobId, writing]);
  useEffect(() => {
    if (!document.body.trim()) return;
    const controller = new AbortController();
    let poll: number | undefined;
    async function load() {
      try {
        const data = await api<Preview & { overflow: boolean }>(
          "/api/workbench/preview",
          readDocument(JSON.parse(payload)),
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setResult({ key: payload, data });
        if (data.pending_gfont)
          poll = window.setTimeout(() => void load(), 1200);
      } catch (e) {
        if (!controller.signal.aborted)
          setResult({
            key: payload,
            error: e instanceof Error ? e.message : "预览失败",
          });
      }
    }
    const debounce = window.setTimeout(() => void load(), 300);
    return () => {
      controller.abort();
      window.clearTimeout(debounce);
      if (poll) window.clearTimeout(poll);
    };
  }, [payload, document.body, revision]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  function change<K extends keyof WritingDocument>(
    key: K,
    value: WritingDocument[K],
  ) {
    if (!locked) setDocument((old) => ({ ...old, [key]: value }));
  }
  function newDocument() {
    if (!locked) {
      setDocument({ ...blank });
      setSaved("");
    }
  }
  function save() {
    try {
      readDocument(document);
      localStorage.setItem(storageKey, payload);
      const url = URL.createObjectURL(
        new Blob([payload], { type: "application/json" }),
      );
      const link = window.document.createElement("a");
      link.href = url;
      link.download =
        (document.name.replace(/[\\/:*?"<>|]/g, "_") || "未命名文档") +
        ".pygmalion.json";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setSaved(payload);
      notify("文档已保存到本机浏览器，并导出文件");
    } catch (e) {
      notify(e instanceof Error ? e.message : "保存失败，请重试", true);
    }
  }
  async function open(input: File) {
    try {
      if (input.size > 256000) throw new Error("文档文件过大");
      const text = await input.text();
      const next = input.name.toLowerCase().endsWith(".txt")
        ? readDocument({
            ...blank,
            name: input.name.replace(/\.txt$/i, ""),
            body: text,
          })
        : readDocument(JSON.parse(text));
      setDocument(next);
      setSaved(JSON.stringify(next));
      notify("文档已打开");
    } catch (e) {
      notify(e instanceof Error ? e.message : "无法打开文档", true);
    }
  }
  const canWrite =
    !!document.body.trim() &&
    !!preview?.can_print &&
    !preview.overflow &&
    !preview.missing.length &&
    !preview.pending_gfont &&
    !error &&
    !locked;
  async function write() {
    if (!canWrite || posting.current) return;
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
          ? "演练任务已开始，正文与参数已冻结"
          : "书写任务已开始，正文与参数已冻结",
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
    change,
    locked,
    dirty,
    preview,
    error,
    canWrite,
    writing,
    jobId,
    newDocument,
    save,
    open,
    write,
    reload: () => {
      setResult(undefined);
      setRevision((v) => v + 1);
    },
  };
}
