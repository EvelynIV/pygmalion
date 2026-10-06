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
  { key: "submissions", label: "投稿管理", href: "/admin" },
  { key: "queue", label: "书写队列", href: "/admin/queue" },
  { key: "machine", label: "机器控制", href: "/admin/machine" },
  { key: "calibration", label: "纸张校准", href: "/admin/calibration" },
  { key: "settings", label: "参数设置", href: "/admin/settings" },
] as const;

export function navigate(href: string) {
  window.history.pushState(null, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
