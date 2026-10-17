import { useEffect, useRef, useState } from "react";
import { useResource, type Fonts } from "./api";
import { Button, Dialog, Link } from "./ui";
import { useWorkbench, type WritingDocument } from "./useWorkbench";
export function Workbench({
  model: m,
  deviceText,
  port,
  busy,
}: {
  model: ReturnType<typeof useWorkbench>;
  deviceText: string;
  port: string;
  busy: boolean;
}) {
  const d = m.document;
  const file = useRef<HTMLInputElement>(null);
  const [discard, setDiscard] = useState<"new" | "open">();
  function perform(action: "new" | "open") {
    if (action === "new") m.newDocument();
    else file.current?.click();
  }
  function request(action: "new" | "open") {
    if (!m.locked) {
      if (m.dirty && (d.body || d.name !== "未命名文档")) setDiscard(action);
      else perform(action);
    }
  }
  const fonts = useResource<Fonts>("/api/fonts");
  const [paperSize, setPaperSize] = useState({ width: 420, height: 594 });
  const stage = useRef<HTMLDivElement>(null);
  const [fitRevision, setFitRevision] = useState(0);
  const width = d.paper === "A4" ? 210 : 148,
    height = d.paper === "A4" ? 297 : 210;
  useEffect(() => {
    if (!stage.current) return;
    const element = stage.current;
    const resize = () => {
      const scale = Math.max(
        0.1,
        Math.min(
          2,
          (element.clientWidth - 24) / width,
          (element.clientHeight - 24) / height,
        ),
      );
      setPaperSize({ width: width * scale, height: height * scale });
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    return () => observer.disconnect();
  }, [width, height, fitRevision]);
  const [svgUrl, setSvgUrl] = useState<{ svg: string; url: string }>();
  useEffect(() => {
    if (!m.preview) return;
    const svg = m.preview.svg,
      url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
    queueMicrotask(() => setSvgUrl({ svg, url }));
    return () => URL.revokeObjectURL(url);
  }, [m.preview]);
  const readyUrl = svgUrl?.svg === m.preview?.svg ? svgUrl?.url : undefined;
  const feedback = !d.body.trim()
    ? "输入正文后生成预览"
    : m.error
      ? "预览失败：" + m.error
      : !m.preview
        ? "正在生成预览…"
        : m.preview.overflow
          ? "正文超出单页范围，请调整排版"
          : m.preview.missing.length
            ? "缺字：" + m.preview.missing.join("、") + "，请更换字库或修改正文"
            : !m.preview.can_print
              ? "没有可绘制的正文，无法书写"
              : busy
                ? "当前正文与纸面参数已固定"
                : "● 内容位于可书写区域内";
  const invalid =
    !!m.error || !!m.preview?.overflow || !!m.preview?.missing.length;
  return (
    <div className="single-page-workbench">
      <div className="document-toolbar">
        <input
          className="document-name"
          aria-label="文档名称"
          value={d.name}
          maxLength={120}
          disabled={m.locked}
          onChange={(e) => m.change("name", e.target.value)}
        />
        <span className="document-save-status muted">
          {m.dirty ? "未保存" : "已保存"}
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
          <Button kind="ghost" disabled={m.locked} onClick={m.save}>
            保存
          </Button>
        </div>
        <Link className="quick-device muted" href="/admin/machine">
          ● {deviceText} · {port === "auto" ? "自动串口" : port}
        </Link>
      </div>
      <div className="single-page-columns">
        <section className="body-editor" aria-labelledby="body-heading">
          <div className="panel-heading">
            <h2 id="body-heading">正文</h2>
            <span className="muted">
              {d.body ? d.body.split(/\r\n|\r|\n/).length : 0} 行 ·{" "}
              {Array.from(d.body.replace(/\s/g, "")).length} 字
            </span>
          </div>
          <textarea
            aria-label="正文"
            value={d.body}
            disabled={m.locked}
            maxLength={20000}
            placeholder="在这里输入需要书写的正文…"
            onChange={(e) => m.change("body", e.target.value)}
            spellCheck={false}
          />
          <p className="muted editor-note">
            保留空行与换行。
            <br />
            文档名称仅用于保存，不会写入纸面。
          </p>
        </section>
        <section className="page-preview" aria-labelledby="preview-heading">
          <div className="panel-heading">
            <h2 id="preview-heading">纸面预览</h2>
            <Button kind="ghost" onClick={() => setFitRevision((v) => v + 1)}>
              适合窗口
            </Button>
          </div>
          <p className="muted paper-description">
            {d.paper} · {width} × {height} mm · 单页
          </p>
          <div className="single-paper-stage" ref={stage}>
            <div
              className={"single-paper " + (invalid ? "invalid" : "")}
              style={paperSize}
            >
              {readyUrl ? (
                <img
                  src={readyUrl}
                  alt={"单页正文真实笔画预览，" + m.preview?.cells + " 个字位"}
                />
              ) : (
                <div
                  className="empty-margin-guide"
                  style={{
                    inset:
                      (100 * d.margin_mm) / height +
                      "% " +
                      (100 * d.margin_mm) / width +
                      "%",
                  }}
                />
              )}
            </div>
          </div>
          <div
            className={"inline-preview-status " + (invalid ? "invalid" : "")}
            role={invalid ? "alert" : "status"}
          >
            <p>{feedback}</p>
            <p className="muted">虚线为页边距，不会写到纸上。</p>
            {m.preview?.warnings.map((warning, index) => (
              <p className="muted" key={index}>
                {warning}
              </p>
            ))}
          </div>
        </section>
        <aside className="single-page-inspector" aria-label="纸面设置">
          <h2>纸面设置</h2>
          <fieldset disabled={m.locked}>
            <label className="compact-field">
              <span>纸张</span>
              <select
                value={d.paper}
                onChange={(e) =>
                  m.change("paper", e.target.value as WritingDocument["paper"])
                }
              >
                <option value="A4">A4 · 210 × 297 mm</option>
                <option value="A5">A5 · 148 × 210 mm</option>
              </select>
            </label>
            <label className="compact-field">
              <span>字体</span>
              <select
                value={d.font}
                onChange={(e) => m.change("font", e.target.value)}
              >
                <option value="">中文单线字库（默认）</option>
                {fonts.data?.fonts.map((f) => (
                  <option key={f.id} value={f.id} disabled={!f.available}>
                    {f.label}
                    {f.available ? "" : "（不可用）"}
                  </option>
                ))}
              </select>
              {fonts.error && (
                <small>
                  字库列表读取失败{" "}
                  <Button kind="ghost" onClick={fonts.reload}>
                    重试
                  </Button>
                </small>
              )}
            </label>
            <div className="compact-field">
              <span>书写方向</span>
              <div className="flat-choices" role="group" aria-label="书写方向">
                {(["horizontal", "vertical"] as const).map((v) => (
                  <Button
                    key={v}
                    kind="ghost"
                    aria-pressed={d.direction === v}
                    className={d.direction === v ? "selected" : ""}
                    onClick={() => m.change("direction", v)}
                  >
                    {v === "horizontal" ? "横排" : "竖排"}
                  </Button>
                ))}
              </div>
            </div>
            <div className="dimension-grid">
              {(
                [
                  ["font_size_mm", "字号", 4, 40],
                  ["line_gap_mm", "行间距", 0, 30],
                  ["char_gap_mm", "字间距", 0, 20],
                  ["margin_mm", "统一页边距", 0, 60],
                ] as const
              ).map(([key, label, min, max]) => (
                <label className="compact-field" key={key}>
                  <span>{label}</span>
                  <div className="dimension-value">
                    <input
                      aria-label={label}
                      type="number"
                      min={min}
                      max={max}
                      step="0.5"
                      value={Number.isFinite(d[key]) ? d[key] : ""}
                      onChange={(e) => m.change(key, e.target.valueAsNumber)}
                    />
                    <span>mm</span>
                  </div>
                </label>
              ))}
            </div>
            <div className="compact-field">
              <span>对齐</span>
              <div className="flat-choices" role="group" aria-label="对齐">
                {(
                  [
                    ["left", "左对齐"],
                    ["center", "居中"],
                    ["right", "右对齐"],
                  ] as const
                ).map(([v, label]) => (
                  <Button
                    kind="ghost"
                    key={v}
                    aria-pressed={d.align === v}
                    className={d.align === v ? "selected" : ""}
                    onClick={() => m.change("align", v)}
                  >
                    {label}
                  </Button>
                ))}
              </div>
            </div>
            <p className="muted">
              仅书写一张纸。
              <br />
              超出范围时暂停书写，调整后再预览。
            </p>
            <Button kind="ghost" onClick={m.reload}>
              重新预览
            </Button>
          </fieldset>
        </aside>
      </div>
      <input
        type="file"
        hidden
        ref={file}
        accept=".json,.txt"
        onChange={(e) => {
          const input = e.target.files?.[0];
          if (input) void m.open(input);
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
          当前修改尚未保存。继续操作将放弃这些修改。
        </Dialog>
      )}
    </div>
  );
}
