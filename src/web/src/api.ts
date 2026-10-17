import { useCallback, useEffect, useState } from "react";

export type SubmissionStatus =
  "pending" | "queued" | "printing" | "done" | "failed";
export type JobStatus = SubmissionStatus | "cancelled";
export interface Submission {
  id: number;
  title: string;
  author: string;
  body: string;
  direction: "vertical" | "horizontal";
  colophon: string;
  signature: string;
  font: string;
  status: SubmissionStatus;
  created_at: string;
  job_count: number;
}
export interface Job {
  job_id: number;
  submission_id: number;
  title?: string;
  author?: string;
  status: JobStatus;
  sent: number;
  total: number;
  percent: number;
  message: string;
  dry_run?: boolean;
  started_at: string;
  finished_at: string;
}
export interface Jobs {
  current: Job | null;
  jobs: Job[];
  dry_run: boolean;
}
export interface Submissions {
  items: Submission[];
  stats: Record<string, number>;
}
export interface Preview {
  svg: string;
  font_size_mm: number;
  requested_size: number;
  shrunk: boolean;
  can_print: boolean;
  cells: number;
  strokes: number;
  missing: string[];
  warnings: string[];
  font: string;
  diagnostics?: import("./canvas").Diagnostic[];
  layout?: {
    element_id: string;
    regions: {
      row?: number;
      col?: number;
      lines: { text: string; x_mm: number; y_mm: number }[];
    }[];
  }[];
}
export interface Fonts {
  default: string;
  fonts: {
    id: string;
    label: string;
    available: boolean;
    chars: number;
    warning?: string;
  }[];
}
export interface Field {
  key: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  help: string;
  value: number;
}
export interface Calibration {
  grid_mm: [number, number];
  paper_mm: [number, number];
  paper_offset_mm: [number, number];
  paper_bounds_mm: [number, number, number, number];
  paper_preset: string;
  fits: boolean;
  paper_presets: {
    id: string;
    name: string;
    group: string;
    width_mm: number;
    height_mm: number;
  }[];
}
export interface Machine {
  port: string;
  baud: number;
  state: Record<string, string>;
  firmware: string[];
  backup: string | null;
}
export interface Ports {
  configured_port: string;
  baud: number;
  ports: { device: string; description: string }[];
}

export async function api<T>(
  path: string,
  data?: object,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: data === undefined ? "GET" : "POST",
    headers: {
      Accept: "application/json",
      ...(data === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: data === undefined ? undefined : JSON.stringify(data),
    signal,
  });
  const result = await response
    .json()
    .catch(() => ({ error: "接口响应无法读取" }));
  if (!response.ok || result.ok === false)
    throw new Error(
      result.error ||
        (typeof result.detail === "string"
          ? result.detail
          : `请求失败（${response.status}）`),
    );
  return result as T;
}

export function useResource<T>(path: string | null, interval = 0) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    path: string;
    data?: T;
    error?: string;
  }>();
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let fetching = false;
    async function refresh() {
      if (fetching || controller.signal.aborted || !path) return;
      fetching = true;
      try {
        const data = await api<T>(path, undefined, controller.signal);
        if (!controller.signal.aborted) setState({ path, data });
      } catch (error) {
        if (!controller.signal.aborted)
          setState((previous) => ({
            path,
            data: previous?.path === path ? previous.data : undefined,
            error: error instanceof Error ? error.message : "连接失败",
          }));
      } finally {
        fetching = false;
      }
    }
    void refresh();
    const timer = interval
      ? window.setInterval(() => void refresh(), interval)
      : undefined;
    return () => {
      controller.abort();
      if (timer) window.clearInterval(timer);
    };
  }, [path, interval, revision]);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  const current = state?.path === path ? state : undefined;
  return {
    data: current?.data,
    error: current?.error,
    loading: !!path && !current,
    reload,
  };
}
