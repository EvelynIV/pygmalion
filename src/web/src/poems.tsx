import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  useResource,
  type Fonts,
  type Job,
  type Jobs,
  type Preview,
  type Submission,
  type Submissions,
} from "./api";
import {
  Button,
  Dialog,
  Heading,
  Link,
  Notice,
  Paper,
  PreviewNotes,
  ResourceState,
  Status,
} from "./ui";
import { navigate, statusNames } from "./navigation";

type Notify = (message: string, error?: boolean) => void;
type Resource<T> = ReturnType<typeof useResource<T>>;
const filters = [
  "all",
  "pending",
  "queued",
  "printing",
  "done",
  "failed",
] as const;

export function SubmissionList({
  resource,
  address,
  copy,
}: {
  resource: Resource<Submissions>;
  address: string;
  copy: () => Promise<void>;
}) {
  const [filter, setFilter] = useState<(typeof filters)[number]>("all");
  const [deleting, setDeleting] = useState<Submission>();
  const [limit, setLimit] = useState(25);
  const all = resource.data?.items || [];
  const items =
    filter === "all" ? all : all.filter((item) => item.status === filter);
  return (
    <>
      <Heading title="投稿管理">查看作品，检查排版后安排书写。</Heading>
      <div className="share-row">
        <span>成员投稿地址</span>
        <code>{address.replace(/^https?:\/\//, "").replace(/\/$/, "")}</code>
        <Button onClick={() => void copy()}>复制地址</Button>
      </div>
      <div className="tabs" role="tablist" aria-label="投稿状态">
        {filters.map((key) => (
          <button
            key={key}
            role="tab"
            aria-selected={filter === key}
            aria-controls="submission-list"
            id={`filter-${key}`}
            onClick={() => {
              setFilter(key);
              setLimit(25);
            }}
            className={filter === key ? "selected" : ""}
          >
            {key === "all" ? "全部" : statusNames[key]}
            <span className="numeric">
              {resource.data?.stats[key === "all" ? "total" : key] ?? "—"}
            </span>
          </button>
        ))}
      </div>
      <ResourceState
        loading={resource.loading}
        error={resource.error}
        retry={resource.reload}
      />
      {!resource.loading && resource.data && (
        <div
          role="tabpanel"
          aria-labelledby={`filter-${filter}`}
          id="submission-list"
        >
          <div className="record-header">
            <span>作品</span>
            <span>状态</span>
            <span>操作</span>
          </div>
          {items.length ? (
            items.slice(0, limit).map((item) => (
              <div className="submission-row" key={item.id}>
                <div className="record-identity">
                  <div className="record-title" title={item.title || "无题"}>
                    {item.title || "无题"}
                  </div>
                  <div
                    className="record-meta"
                    title={`${item.author} · ${item.created_at}`}
                  >
                    #{String(item.id).padStart(4, "0")} · {item.author} ·{" "}
                    {item.body.replace(/[\s，。！？、；：]/g, "").length} 字 ·{" "}
                    {item.direction === "vertical" ? "竖排" : "横排"}
                  </div>
                </div>
                <Status value={item.status} />
                <div className="record-actions">
                  <Link
                    className="button secondary preview-action"
                    href={`/admin/layout/${item.id}`}
                  >
                    排版预览
                  </Link>
                  <Button
                    kind="ghost"
                    className="delete-action"
                    onClick={() => setDeleting(item)}
                  >
                    删除
                  </Button>
                </div>
              </div>
            ))
          ) : (
            <div className="empty">
              <p>
                {filter === "all"
                  ? "暂无投稿"
                  : `暂无${statusNames[filter]}的投稿`}
              </p>
              <span className="muted">
                分享成员投稿地址，收到作品后会显示在这里。
              </span>
            </div>
          )}
          <div className="list-summary">
            <span>
              显示 {Math.min(items.length, limit)} / {items.length} 篇投稿
            </span>
            {items.length > limit && (
              <Button
                kind="ghost"
                onClick={() => setLimit((value) => value + 25)}
              >
                加载更多
              </Button>
            )}
          </div>
        </div>
      )}
      {deleting && (
        <Dialog
          title={`删除「${deleting.title || "无题"}」`}
          confirmLabel="确认删除"
          danger
          close={() => setDeleting(undefined)}
          confirm={async () => {
            await api(`/api/admin/delete/${deleting.id}`, {});
            resource.reload();
          }}
        >
          删除后无法恢复。已入队的任务保留冻结内容与任务记录，书写将继续；如需停止，请先中止任务。
        </Dialog>
      )}
    </>
  );
}

export function QueuePage({
  resource,
  submissions,
  cancel,
}: {
  resource: Resource<Jobs>;
  submissions: Submission[];
  cancel: (job: Job) => void;
}) {
  return (
    <>
      <Heading title="书写队列">
        查看指令发送进度，管理排队任务与书写记录。
      </Heading>
      <ResourceState
        loading={resource.loading}
        error={resource.error}
        retry={resource.reload}
      />
      {resource.data?.dry_run && (
        <Notice>
          当前书写任务使用演练模式，不发送书写指令；手动机器操作仍会执行真实动作。
        </Notice>
      )}
      {resource.data?.jobs.length
        ? resource.data.jobs.map((job) => {
            const sub = submissions.find(
              (item) => item.id === job.submission_id,
            );
            return (
              <div key={job.job_id} className="job-row">
                <div className="record-identity">
                  <div className="record-title">
                    {job.title || sub?.title || "无题"}{" "}
                    <span className="muted">#{job.job_id}</span>
                  </div>
                  <div className="record-meta">
                    {job.author || sub?.author || "—"} ·{" "}
                    {job.started_at || "等待开始"}
                    {job.dry_run ? " · 演练" : ""}
                  </div>
                  {job.message && (
                    <p
                      className={
                        job.status === "failed" ? "error-text" : "muted"
                      }
                    >
                      {job.message}
                    </p>
                  )}
                </div>
                <Status value={job.status} />
                <div className="job-progress">
                  <span>指令发送进度 {Math.round(job.percent)}%</span>
                  <progress
                    value={job.percent}
                    max="100"
                    aria-label={`任务 ${job.job_id} 指令发送进度`}
                  />
                  <small>
                    {job.sent} / {job.total} 条指令
                  </small>
                </div>
                {job.status === "queued" || job.status === "printing" ? (
                  <Button onClick={() => cancel(job)}>中止</Button>
                ) : sub ? (
                  <Link
                    className="button secondary"
                    href={`/admin/layout/${sub.id}`}
                  >
                    重新书写
                  </Link>
                ) : (
                  <span className="muted">投稿已删除</span>
                )}
              </div>
            );
          })
        : !resource.loading &&
          !resource.error && (
            <div className="empty">
              队列为空。<Link href="/admin">返回投稿管理</Link>
            </div>
          )}
    </>
  );
}

function FontSelect({
  value,
  change,
  fonts,
}: {
  value: string;
  change: (value: string) => void;
  fonts?: Fonts;
}) {
  return (
    <label className="field">
      <span>字体</span>
      <select value={value} onChange={(event) => change(event.target.value)}>
        <option value="">默认字库</option>
        {fonts?.fonts.map((font) => (
          <option key={font.id} value={font.id} disabled={!font.available}>
            {font.label}
            {font.available ? "" : "（不可用）"}
          </option>
        ))}
      </select>
    </label>
  );
}
function FormatFields({
  direction,
  colophon,
  change,
}: {
  direction: string;
  colophon: string;
  change: (key: string, value: string) => void;
}) {
  return (
    <>
      <label className="field">
        <span>方向</span>
        <select
          value={direction}
          onChange={(event) => change("direction", event.target.value)}
        >
          <option value="vertical">竖排 · 从右向左</option>
          <option value="horizontal">横排 · 从左向右</option>
        </select>
      </label>
      <label className="field">
        <span>落款</span>
        <select
          value={colophon}
          onChange={(event) => change("colophon", event.target.value)}
        >
          <option value="none">不落款</option>
          <option value="author">仅署名</option>
          <option value="full">完整落款</option>
          <option value="seal">印章式落款</option>
        </select>
      </label>
    </>
  );
}

export function LayoutPage({
  id,
  notify,
  refresh,
}: {
  id: number;
  submissions: Submission[];
  notify: Notify;
  refresh: () => void;
}) {
  const resource = useResource<{
    item: Submission;
    layout: Record<string, number>;
  }>(`/api/admin/submission/${id}`);
  return (
    <>
      <ResourceState
        loading={resource.loading}
        error={resource.error}
        retry={resource.reload}
      />
      {resource.data && (
        <LayoutEditor
          submission={resource.data.item}
          defaults={resource.data.layout}
          notify={notify}
          refresh={refresh}
        />
      )}
    </>
  );
}
function LayoutEditor({
  submission,
  defaults,
  notify,
  refresh,
}: {
  submission: Submission;
  defaults: Record<string, number>;
  notify: Notify;
  refresh: () => void;
}) {
  const initial = {
    direction: submission.direction,
    colophon: submission.colophon,
    font: submission.font,
    font_size_mm: defaults.font_size_mm || 12,
    char_gap_mm: defaults.char_gap_mm ?? 1,
    line_gap_mm: defaults.line_gap_mm ?? 5,
    title_scale: defaults.title_scale || 1.25,
    handwriting: true,
    handwriting_strength: 1,
  };
  const [form, setForm] = useState(initial);
  const [submitted, setSubmitted] = useState(false);
  const [printing, setPrinting] = useState(false);
  const [mobileTab, setMobileTab] = useState("preview");
  const query = new URLSearchParams(
    Object.entries(form).map(([key, value]) => [key, String(value)]),
  ).toString();
  const [debounced, setDebounced] = useState(query);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const preview = useResource<Preview>(
    `/api/admin/preview/${submission.id}?fast=1&${debounced}`,
    2500,
  );
  const fonts = useResource<Fonts>("/api/fonts");
  const final = query === debounced ? preview.data : undefined;
  const change = (key: string, value: string | number | boolean) => {
    setForm((old) => ({ ...old, [key]: value }));
    setSubmitted(false);
  };
  async function print() {
    setPrinting(true);
    try {
      await api(`/api/admin/print/${submission.id}`, form);
      setSubmitted(true);
      refresh();
      notify("任务已入队，排版与书写参数已冻结");
    } catch (e) {
      notify(e instanceof Error ? e.message : "入队失败", true);
    } finally {
      setPrinting(false);
    }
  }
  return (
    <>
      <Heading
        title="排版工作台"
        action={
          <Link className="button ghost" href="/admin">
            返回投稿管理
          </Link>
        }
      >
        {submission.title || "无题"} / {submission.author} ·
        只调整本次预览与书写任务。
      </Heading>
      <div className="mobile-layout-tabs">
        <Button
          kind={mobileTab === "preview" ? "primary" : "ghost"}
          onClick={() => setMobileTab("preview")}
        >
          预览
        </Button>
        <Button
          kind={mobileTab === "parameters" ? "primary" : "ghost"}
          onClick={() => setMobileTab("parameters")}
        >
          参数
        </Button>
      </div>
      <div className={`layout-workspace mobile-${mobileTab}`}>
        <section className="preview-panel">
          <Paper preview={final} />
          <ResourceState
            loading={!final && !preview.error}
            error={preview.error}
            retry={preview.reload}
          />
          <PreviewNotes preview={final} />
          {final && (
            <p className="muted">
              {final.cells} 个字位 · {final.strokes} 条笔画 · 实际字号{" "}
              {final.font_size_mm} mm
            </p>
          )}
        </section>
        <aside className="inspector" aria-label="排版参数">
          <h2>排版参数</h2>
          <FontSelect
            value={form.font}
            change={(value) => change("font", value)}
            fonts={fonts.data}
          />
          <FormatFields
            direction={form.direction}
            colophon={form.colophon}
            change={change}
          />
          {(
            [
              ["font_size_mm", "字号", 4, 40, 0.5, "mm"],
              ["char_gap_mm", "字间距", 0, 20, 0.5, "mm"],
              ["line_gap_mm", "行间距", 0, 30, 0.5, "mm"],
              ["title_scale", "标题倍率", 1, 2.5, 0.05, "倍"],
              ["handwriting_strength", "手写感强度", 0, 3, 0.1, ""],
            ] as const
          ).map(([key, label, min, max, step, unit]) => (
            <label className="field" key={key}>
              <span>
                {label}
                <small>{unit}</small>
              </span>
              <input
                type="number"
                value={form[key]}
                min={min}
                max={max}
                step={step}
                onChange={(event) => change(key, Number(event.target.value))}
              />
            </label>
          ))}
          <label className="check-field">
            <input
              type="checkbox"
              checked={form.handwriting}
              onChange={(event) => change("handwriting", event.target.checked)}
            />
            启用手写感
          </label>
          <div className="inspector-actions">
            <Button
              onClick={() => {
                setForm(initial);
                setSubmitted(false);
              }}
            >
              恢复默认
            </Button>
            <Button onClick={preview.reload}>重新预览</Button>
          </div>
          <Button
            kind="primary"
            className="full-width"
            disabled={
              !final?.can_print ||
              final.pending_gfont ||
              !!preview.error ||
              printing ||
              submitted
            }
            onClick={() => void print()}
          >
            {printing ? "正在入队…" : submitted ? "已加入队列" : "开始书写"}
          </Button>
          {submitted && <Link href="/admin/queue">查看书写队列</Link>}
          <p className="muted">入队时冻结当前参数。再次书写创建新任务。</p>
        </aside>
      </div>
    </>
  );
}

export function PublicSubmission() {
  const fonts = useResource<Fonts>("/api/fonts");
  const [form, setForm] = useState({
    author: "",
    title: "",
    body: "",
    direction: "vertical",
    colophon: "none",
    signature: "",
    font: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const change = (key: string, value: string) =>
    setForm((old) => ({ ...old, [key]: value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ id: number }>("/api/submit", form);
      navigate(`/result/${result.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "投稿失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="public-content">
      <Heading title="成员投稿">写下诗词，交由 Pygmalion 落笔成篇。</Heading>
      <form
        onSubmit={(event) => void submit(event)}
        className="submission-form"
      >
        <div className="two-fields">
          <label className="field">
            <span>姓名</span>
            <input
              required
              maxLength={80}
              autoComplete="name"
              value={form.author}
              onChange={(event) => change("author", event.target.value)}
            />
          </label>
          <label className="field">
            <span>
              诗题 <small>可选</small>
            </span>
            <input
              maxLength={200}
              value={form.title}
              onChange={(event) => change("title", event.target.value)}
            />
          </label>
        </div>
        <label className="field">
          <span>
            正文 <small>{form.body.length} / 2000</small>
          </span>
          <textarea
            required
            maxLength={2000}
            rows={9}
            placeholder="每句可单独换行"
            value={form.body}
            onChange={(event) => change("body", event.target.value)}
          />
        </label>
        <div className="two-fields">
          <FontSelect
            value={form.font}
            change={(value) => change("font", value)}
            fonts={fonts.data}
          />
          <label className="field">
            <span>
              署名 <small>可选，默认使用姓名</small>
            </span>
            <input
              maxLength={200}
              value={form.signature}
              onChange={(event) => change("signature", event.target.value)}
            />
          </label>
          <FormatFields
            direction={form.direction}
            colophon={form.colophon}
            change={change}
          />
        </div>
        {error && <Notice error>{error}</Notice>}
        <Button kind="primary" type="submit" disabled={busy}>
          {busy ? "正在提交…" : "提交诗词"}
        </Button>
      </form>
    </div>
  );
}
export function ResultPage({ id }: { id: number }) {
  const preview = useResource<Preview>(`/api/my/${id}?fast=1`, 2500);
  return (
    <div className="public-content">
      <Heading title="投稿成功">
        投稿编号 #{String(id).padStart(4, "0")}。作品已提交，等待安排书写。
      </Heading>
      <ResourceState
        loading={preview.loading}
        error={preview.error}
        retry={preview.reload}
      />
      <Paper preview={preview.data} />
      <PreviewNotes preview={preview.data} />
      <Link className="button secondary" href="/">
        再次投稿
      </Link>
    </div>
  );
}
