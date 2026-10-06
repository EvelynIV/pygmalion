import { useEffect, useState } from "react";
import { api, useResource, type Job, type Jobs, type Submissions } from "./api";
import { Button, DesignIcon, Dialog, Link, Status } from "./ui";
import { navigation } from "./navigation";
import {
  SubmissionList,
  LayoutPage,
  PublicSubmission,
  ResultPage,
  QueuePage,
} from "./poems";
import { MachinePage, CalibrationPage, SettingsPage } from "./machine";

export default function App() {
  const [path, setPath] = useState(window.location.pathname);
  const [night, setNight] = useState(
    () => localStorage.getItem("pygmalion-theme") === "night",
  );
  const [toast, setToast] = useState<{ text: string; error?: boolean }>();
  const [cancel, setCancel] = useState<Job>();
  const [estopping, setEstopping] = useState(false);
  const jobs = useResource<Jobs>("/api/admin/jobs", 1000);
  const submissions = useResource<Submissions>("/api/admin/submissions", 3000);
  const net = useResource<{ urls: string[] }>("/api/admin/net");
  const active = path.includes("/layout")
    ? "submissions"
    : navigation.find((item) => item.href === path)?.key || "submissions";
  const isAdmin = path === "/admin" || path.startsWith("/admin/");
  const pageLabel = path.includes("/layout")
    ? "排版工作台"
    : navigation.find((item) => item.key === active)?.label || "投稿管理";
  const submitUrl =
    net.data?.urls.find((url) =>
      /^http:\/\/(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(url),
    ) ||
    net.data?.urls[0] ||
    window.location.origin;
  const current = jobs.data?.current;
  const currentRecord = jobs.data?.jobs.find(
    (job) => job.job_id === current?.job_id,
  );
  const currentSubmission = submissions.data?.items.find(
    (item) => item.id === current?.submission_id,
  );
  const notify = (text: string, error = false) => setToast({ text, error });
  useEffect(() => {
    const listener = () => setPath(window.location.pathname);
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = night ? "night" : "day";
    localStorage.setItem("pygmalion-theme", night ? "night" : "day");
  }, [night]);
  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(
      () => setToast(undefined),
      toast.error ? 7000 : 3200,
    );
    return () => window.clearTimeout(timeout);
  }, [toast]);
  async function copyAddress() {
    try {
      await navigator.clipboard.writeText(submitUrl);
      notify("投稿地址已复制");
    } catch {
      notify("未能复制，请手动复制显示的投稿地址", true);
    }
  }
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
  const layoutId = Number(path.match(/^\/admin\/layout\/(\d+)$/)?.[1]);
  const resultId = Number(path.match(/^\/result\/(\d+)$/)?.[1]);
  return (
    <>
      <a href="#main" className="skip-link">
        跳到主要内容
      </a>
      {isAdmin ? (
        <div className="app-shell">
          <aside className="sidebar" aria-label="工作区导航">
            <div className="brand">
              <span>Pygmalion</span>
              <small>诗词自动书写</small>
            </div>
            <nav>
              {navigation.map((item) => (
                <Link
                  key={item.key}
                  href={item.href}
                  className={`nav-item ${active === item.key ? "selected" : ""}`}
                  aria-current={active === item.key ? "page" : undefined}
                >
                  <DesignIcon name={item.key} selected={active === item.key} />
                  {item.label}
                </Link>
              ))}
            </nav>
            <div className="submission-access">
              <span>成员投稿入口</span>
              <code title={submitUrl}>
                {submitUrl.replace(/^https?:\/\//, "").replace(/\/$/, "")}
              </code>
              <Button onClick={copyAddress}>复制投稿地址</Button>
              <Link href="/" className="return-submit">
                返回投稿
              </Link>
            </div>
          </aside>
          <div className="workspace">
            <header className="header">
              <div className="bounded header-inner">
                <div className="breadcrumb">
                  <span>工作区</span>
                  <span>/</span>
                  <span aria-current="page">{pageLabel}</span>
                </div>
                <div className="header-actions">
                  <Button
                    kind="ghost"
                    onClick={() => setNight(!night)}
                    aria-label={`切换为${night ? "白昼" : "夜间"}主题`}
                  >
                    {night ? "夜间" : "白昼"}
                  </Button>
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
            <main id="main" className="main">
              <div className="bounded">
                {path === "/admin" ? (
                  <SubmissionList
                    resource={submissions}
                    address={submitUrl}
                    copy={copyAddress}
                  />
                ) : layoutId ? (
                  <LayoutPage
                    key={layoutId}
                    id={layoutId}
                    submissions={submissions.data?.items || []}
                    notify={notify}
                    refresh={jobs.reload}
                  />
                ) : path === "/admin/queue" ? (
                  <QueuePage
                    resource={jobs}
                    submissions={submissions.data?.items || []}
                    cancel={setCancel}
                  />
                ) : path === "/admin/machine" ? (
                  <MachinePage busy={!!current} notify={notify} />
                ) : path === "/admin/calibration" ? (
                  <CalibrationPage notify={notify} />
                ) : path === "/admin/settings" ? (
                  <SettingsPage busy={!!current} notify={notify} />
                ) : (
                  <div className="empty">
                    页面不存在 <Link href="/admin">返回投稿管理</Link>
                  </div>
                )}
              </div>
            </main>
            <footer className="task-bar">
              <div className="bounded task-inner">
                <div className="current-task">
                  {current ? (
                    <>
                      <Status value={current.status} label="当前书写" />
                      <span
                        className="task-title"
                        title={currentRecord?.title || currentSubmission?.title}
                      >
                        {currentRecord?.title ||
                          currentSubmission?.title ||
                          `投稿 #${current.submission_id}`}
                      </span>
                      <span className="muted task-author">
                        /{" "}
                        {currentRecord?.author ||
                          currentSubmission?.author ||
                          "—"}
                      </span>
                      {jobs.data?.dry_run && (
                        <span className="muted">演练</span>
                      )}
                    </>
                  ) : (
                    <span className="muted">
                      {jobs.error
                        ? "任务状态读取失败"
                        : jobs.loading
                          ? "正在读取任务"
                          : "当前无书写任务"}
                    </span>
                  )}
                </div>
                <div className="task-progress">
                  <span>指令发送进度</span>
                  <progress
                    value={current?.percent || 0}
                    max="100"
                    aria-label="指令发送进度"
                  />
                  <span className="numeric">
                    {current ? `${Math.round(current.percent)}%` : "—"}
                  </span>
                </div>
                <Button
                  disabled={!current}
                  onClick={() => current && setCancel(current)}
                >
                  中止
                </Button>
                <Button
                  kind="danger"
                  disabled={estopping}
                  onClick={() => void estop()}
                >
                  急停
                </Button>
              </div>
            </footer>
          </div>
        </div>
      ) : (
        <div className="public-shell">
          <header className="public-header">
            <div className="brand">
              <span>Pygmalion</span>
              <small>诗词自动书写</small>
            </div>
            <div className="header-actions">
              <Button kind="ghost" onClick={() => setNight(!night)}>
                {night ? "夜间" : "白昼"}
              </Button>
              <Link href="/admin" className="button secondary">
                管理工作区
              </Link>
            </div>
          </header>
          <main id="main">
            {path === "/" ? (
              <PublicSubmission />
            ) : resultId ? (
              <ResultPage key={resultId} id={resultId} />
            ) : (
              <div className="empty">
                页面不存在 <Link href="/">返回投稿</Link>
              </div>
            )}
          </main>
        </div>
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
            submissions.reload();
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
