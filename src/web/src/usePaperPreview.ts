import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Preview } from "./api";
import { readDocument, type WritingDocument } from "./canvas";

type PaperPreview = Preview & { overflow: boolean };
type PreviewState = {
  scope: string;
  result?: { key: string; data?: PaperPreview; error?: string };
  image?: { key: string; url: string };
};

function layoutKey(document: WritingDocument) {
  return JSON.stringify({
    ...document,
    name: undefined,
    elements: document.elements.map((element) =>
      element.type === "text"
        ? { ...element, text: undefined }
        : element.type !== "table"
          ? element
          : {
              ...element,
              cells: element.cells.map((cell) => ({
                ...cell,
                text: undefined,
              })),
            },
    ),
  });
}

/** 显示缓存可以沿用；书写校验必须匹配当前文档及请求。 */
export function usePaperPreview(
  document: WritingDocument,
  generation: number,
  revision: number,
) {
  const payload = JSON.stringify(document);
  const scope = `${generation}:${layoutKey(document)}`;
  const requestKey = `${generation}:${revision}:${payload}`;
  const count = document.elements.length;
  const [state, setState] = useState<PreviewState>({ scope });
  // 换文档或布局时永久丢弃不兼容的图像，撤销回来也不能复用已释放的 URL。
  if (state.scope !== scope) setState({ scope });
  const urls = useRef(new Set<string>());
  const pending = useRef(new Set<string>());
  const release = useCallback((url: string) => {
    if (urls.current.delete(url)) URL.revokeObjectURL(url);
  }, []);

  useEffect(() => {
    const owned = urls.current;
    return () => {
      for (const url of owned) release(url);
    };
  }, [release]);
  const shownUrl = state.scope === scope ? state.image?.url : undefined;
  useEffect(() => {
    // 提交新图或清空画布后，释放所有不再显示、也不在解码的资源。
    for (const url of urls.current) {
      if (url !== shownUrl && !pending.current.has(url)) release(url);
    }
  }, [shownUrl, scope, release]);

  useEffect(() => {
    if (!count) return;
    const controller = new AbortController();
    let candidate: string | undefined;
    let loader: HTMLImageElement | undefined;
    function discard() {
      if (candidate) {
        pending.current.delete(candidate);
        release(candidate);
      }
      candidate = undefined;
    }
    async function load() {
      try {
        const data = await api<PaperPreview>(
          "/api/workbench/preview",
          readDocument(JSON.parse(payload)),
          controller.signal,
        );
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(
          new Blob([data.svg], { type: "image/svg+xml" }),
        );
        urls.current.add(url);
        pending.current.add(url);
        candidate = url;
        loader = new Image();
        loader.src = url;
        try {
          await loader.decode();
        } catch {
          throw new Error("预览图片加载失败，请重试");
        }
        if (controller.signal.aborted) return;
        // 图像已就绪时同时发布显示内容和校验结果，避免新图加载期间清屏。
        pending.current.delete(url);
        candidate = undefined;
        loader = undefined;
        setState({
          scope,
          image: { key: requestKey, url },
          result: { key: requestKey, data },
        });
      } catch (error) {
        if (!controller.signal.aborted)
          setState((previous) =>
            previous.scope === scope
              ? {
                  ...previous,
                  result: {
                    key: requestKey,
                    error: error instanceof Error ? error.message : "预览失败",
                  },
                }
              : previous,
          );
      } finally {
        discard();
      }
    }
    const timer = window.setTimeout(() => void load(), 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      if (loader) loader.src = "";
      discard();
    };
  }, [count, payload, scope, requestKey, release]);

  const result =
    state.scope === scope && state.result?.key === requestKey
      ? state.result
      : undefined;
  return { preview: result?.data, error: result?.error, previewUrl: shownUrl };
}
