import { useEffect, useState } from "react";
import { api, useResource, type Job, type Jobs, type Ports } from "./api";
import { Button, Dialog, Link, WorkspaceIcon } from "./ui";
import { navigation } from "./navigation";
import { MachinePage, SettingsPage } from "./machine";
import { Workbench } from "./workbench";
import { useWorkbench } from "./useWorkbench";

export default function App() {
  const [documentHost, setDocumentHost] = useState<HTMLDivElement | null>(null);
  const [help, setHelp] = useState(false);
  const [deviceState, setDeviceState] = useState("设备状态未读取");
  const [path, setPath] = useState(window.location.pathname);
  const [night, setNight] = useState(() =>
    ["night", "abyssus"].includes(
      localStorage.getItem("pygmalion-theme") || "",
    ),
  );
  const [expanded, setExpanded] = useState(
    () => localStorage.getItem("pygmalion-sidebar") === "expanded",
  );
  const [toast, setToast] = useState<{ text: string; error?: boolean }>();
  const [cancel, setCancel] = useState<Job>();
  const [estopping, setEstopping] = useState(false);
  const jobs = useResource<Jobs>("/api/admin/jobs", 500);
  const ports = useResource<Ports>("/api/admin/serial-ports", 5000);
  const current =
    jobs.data?.current || jobs.data?.jobs.find((j) => j.status === "queued");
  const busy = !!current;
  const active = navigation.find((item) => item.href === path)?.key || "layout";
  const pageLabel = navigation.find((item) => item.key === active)!.label;
  const notify = (text: string, error = false) => setToast({ text, error });
  const doc = useWorkbench({
    busy,
    jobs: jobs.data?.jobs,
    notify,
    refresh: jobs.reload,
  });
  const last = jobs.data?.jobs.find((j) => j.job_id === doc.jobId);
  const record = current
    ? jobs.data?.jobs.find((j) => j.job_id === current.job_id)
    : last;
  useEffect(() => {
    const listener = () => setPath(window.location.pathname);
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = night ? "abyssus" : "vallum";
    localStorage.setItem("pygmalion-theme", night ? "abyssus" : "vallum");
  }, [night]);
  useEffect(() => {
    localStorage.setItem(
      "pygmalion-sidebar",
      expanded ? "expanded" : "collapsed",
    );
  }, [expanded]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(
      () => setToast(undefined),
      toast.error ? 7000 : 3200,
    );
    return () => window.clearTimeout(timer);
  }, [toast]);
  async function estop() {
    setEstopping(true);
    try {
      const result = await api<{ state: string }>("/api/admin/estop", {});
      notify(`急停：${result.state}`);
      jobs.reload();
    } catch (error) {
      notify(error instanceof Error ? error.message : "急停失败", true);
    } finally {
      setEstopping(false);
    }
  }
  return (
    <>
      <a href="#main" className="skip-link">
        跳到主要内容
      </a>
      <div
        className={`app-shell console-v1 ${expanded ? "nav-expanded" : "nav-collapsed"}`}
      >
        <aside className="sidebar" aria-label="工作区导航">
          <Button
            kind="ghost"
            className="sidebar-toggle"
            aria-label={expanded ? "收起导航" : "展开导航"}
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            <WorkspaceIcon name="menu" size={18} />
          </Button>
          <div className="brand">
            <span>{expanded ? "Pygmalion" : "P"}</span>
            {expanded && <small>写字机控制台</small>}
          </div>
          <nav>
            {navigation.map((item) => (
              <Link
                key={item.key}
                href={item.href}
                className={`nav-item ${active === item.key ? "selected" : ""}`}
                aria-current={active === item.key ? "page" : undefined}
              >
                <WorkspaceIcon
                  name={`${item.key}${active === item.key ? "-selected" : ""}`}
                />
                <span className={expanded ? "" : "sr-only"}>{item.label}</span>
                {!expanded && (
                  <span className="nav-tooltip" aria-hidden="true">
                    {item.label}
                  </span>
                )}
              </Link>
            ))}
          </nav>
          <Button
            className="sidebar-help"
            kind="ghost"
            aria-label="快捷键与帮助"
            onClick={() => setHelp(true)}
          >
            <WorkspaceIcon name="help" />
          </Button>
        </aside>
        <div className="workspace">
          <header className="header">
            <div className="header-inner">
              <div className="breadcrumb">
                <span>Pygmalion</span>
                <strong>/ {pageLabel}</strong>
              </div>
              {active === "layout" && (
                <div className="document-host" ref={setDocumentHost} />
              )}
              <div className="header-actions">
                <Link
                  href="/admin/machine"
                  className="header-device"
                  title={`串口：${ports.data?.configured_port || "尚未读取"}`}
                >
                  ○{" "}
                  {jobs.error
                    ? "任务状态不可用"
                    : busy
                      ? "书写中"
                      : deviceState}
                </Link>
                <Button
                  kind="ghost"
                  onClick={() => setNight(!night)}
                  aria-label={`切换为${night ? "白昼" : "夜间"}主题`}
                >
                  主题
                </Button>
                {active === "layout" && (
                  <Button
                    className="start-writing"
                    kind="primary"
                    disabled={
                      !doc.canWrite || busy || !!jobs.error || jobs.loading
                    }
                    onClick={() => void doc.write()}
                  >
                    {doc.writing ? "正在准备" : busy ? "书写中" : "开始书写"}
                  </Button>
                )}
                {active !== "layout" && <span className="start-reserved" />}
                <Button
                  kind="danger"
                  onClick={() => void estop()}
                  disabled={estopping}
                >
                  {estopping ? "急停中" : "急停"}
                </Button>
              </div>
            </div>
          </header>
          <main
            id="main"
            className={`main ${active === "layout" ? "workbench-main" : "utility-main"}`}
          >
            {active === "layout" ? (
              <Workbench model={doc} documentHost={documentHost} busy={busy} />
            ) : (
              <div className="bounded">
                {active === "machine" ? (
                  <MachinePage
                    busy={busy}
                    notify={notify}
                    onState={setDeviceState}
                  />
                ) : (
                  <SettingsPage busy={busy} notify={notify} />
                )}
              </div>
            )}
          </main>
          <footer className="task-bar">
            <div className="task-inner">
              <div className="current-task" role="status">
                {jobs.error ? (
                  "任务状态读取失败"
                ) : current ? (
                  <>
                    <span>
                      {current.status === "queued" ? "准备书写" : "书写中"} ·{" "}
                      {record?.title || "当前文档"}
                      {jobs.data?.dry_run ? " · 演练" : ""}
                    </span>
                    <span>
                      指令发送进度 {Math.round(current.percent)}%
                      {current.percent >= 100 ? " · 等待机器结束运动" : ""}
                    </span>
                  </>
                ) : last ? (
                  `${last.status === "done" ? "书写完成" : last.status === "cancelled" ? "已中止" : "书写失败"} · ${last.title || doc.document.name}${last.message ? ` · ${last.message}` : ""}`
                ) : (
                  "当前无书写任务"
                )}
              </div>
              <Button
                disabled={!current}
                onClick={() => current && setCancel(current)}
              >
                中止
              </Button>
            </div>
          </footer>
        </div>
      </div>
      {help && (
        <Dialog
          title="快捷键与帮助"
          confirmLabel="知道了"
          close={() => setHelp(false)}
          confirm={async () => {}}
        >
          <p>
            单击文本框或单元格输入，输入即保存到本机。Ctrl+Z 撤销，Ctrl+Y
            重做。非文字输入时 Ctrl / ⌘ + A 全选纸面元素，Backspace / Delete
            删除，Esc 取消选择。
          </p>
          <p>
            H 切换小手拖动，Esc 返回选择；Ctrl / ⌘ + 滚轮仅缩放纸张。Shift
            点击可扩展单元格选区。
          </p>
          <p>
            纸张左上角为 0, 0
            mm。虚线和选择框不落笔；勾选“实际书写表格线”后才书写表格框线，默认开启。
          </p>
        </Dialog>
      )}
      {cancel && (
        <Dialog
          title="中止书写任务"
          confirmLabel="确认中止"
          close={() => setCancel(undefined)}
          confirm={async () => {
            await api(`/api/admin/cancel/${cancel.job_id}`, {});
            notify("已请求中止；缓冲区中的动作可能仍会执行");
            jobs.reload();
          }}
        >
          停止发送后续指令，已发送到机器缓冲区的动作可能继续数秒。重新书写会创建新任务。
        </Dialog>
      )}
      {toast && (
        <div
          className={`toast ${toast.error ? "error" : ""}`}
          role={toast.error ? "alert" : "status"}
        >
          {toast.text}
          <Button
            kind="ghost"
            onClick={() => setToast(undefined)}
            aria-label="关闭消息"
          >
            ×
          </Button>
        </div>
      )}
    </>
  );
}
