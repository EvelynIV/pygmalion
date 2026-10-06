import type { JobStatus } from "./api";

export const statusNames: Record<JobStatus, string> = {
  pending: "未写",
  queued: "排队中",
  printing: "书写中",
  done: "已写",
  failed: "失败",
  cancelled: "已中止",
};
export const navigation = [
  { key: "layout", label: "书写工作台", href: "/" },
  { key: "machine", label: "机器控制", href: "/admin/machine" },
  { key: "settings", label: "参数设置", href: "/admin/settings" },
] as const;

export function navigate(href: string) {
  window.history.pushState(null, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
