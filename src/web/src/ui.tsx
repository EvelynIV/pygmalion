import {
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import type { JobStatus, Preview } from "./api";
import { navigate, statusNames } from "./navigation";
export function Link({
  href,
  children,
  className,
  ...props
}: {
  href: string;
  children: ReactNode;
  className?: string;
  "aria-current"?: "page";
}) {
  return (
    <a
      href={href}
      className={className}
      {...props}
      onClick={(event) => {
        if (
          event.button === 0 &&
          !event.metaKey &&
          !event.ctrlKey &&
          !event.shiftKey &&
          !event.altKey
        ) {
          event.preventDefault();
          navigate(href);
        }
      }}
    >
      {children}
    </a>
  );
}
export function Button({
  kind = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  kind?: "secondary" | "ghost" | "danger" | "primary";
}) {
  return (
    <button
      type="button"
      className={`button ${kind} ${className}`}
      {...props}
    />
  );
}
export function DesignIcon({
  name,
  selected = false,
}: {
  name: string;
  selected?: boolean;
}) {
  const asset =
    name === "submissions"
      ? `${name}${selected ? "" : "-default"}`
      : `${name}${selected ? "-selected" : ""}`;
  return (
    <span className="design-icon" aria-hidden="true">
      <img
        className="light-asset"
        width="16"
        height="16"
        src={`/design/${asset}.svg`}
        alt=""
      />
      <img
        className="night-asset"
        width="16"
        height="16"
        src={`/design/${asset}-night.svg`}
        alt=""
      />
    </span>
  );
}
export function Status({ value, label }: { value: JobStatus; label?: string }) {
  const asset = value === "cancelled" ? "pending" : value;
  return (
    <span className={`status ${value}`}>
      <img
        className="light-asset"
        width="5"
        height="5"
        src={`/design/${asset}.svg`}
        alt=""
      />
      <img
        className="night-asset"
        width="5"
        height="5"
        src={`/design/${asset}-night.svg`}
        alt=""
      />
      {label || statusNames[value]}
    </span>
  );
}
export function Heading({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        <p>{children}</p>
      </div>
      {action}
    </div>
  );
}
export function Notice({
  children,
  error = false,
}: {
  children: ReactNode;
  error?: boolean;
}) {
  return (
    <div
      className={`notice ${error ? "error" : ""}`}
      role={error ? "alert" : "status"}
    >
      {children}
    </div>
  );
}
export function ResourceState({
  loading,
  error,
  retry,
}: {
  loading: boolean;
  error?: string;
  retry: () => void;
}) {
  if (error)
    return (
      <Notice error>
        {error}
        <Button onClick={retry}>重试</Button>
      </Notice>
    );
  if (loading)
    return (
      <div className="empty" role="status">
        正在读取…
      </div>
    );
  return null;
}
export function Dialog({
  title,
  children,
  confirm,
  confirmLabel,
  close,
  danger = false,
}: {
  title: string;
  children: ReactNode;
  confirm: () => Promise<void>;
  confirmLabel: string;
  close: () => void;
  danger?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.showModal();
    return () => {
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby="dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) close();
      }}
    >
      <h2 id="dialog-title">{title}</h2>
      <div className="dialog-detail">{children}</div>
      {error && <Notice error>{error}</Notice>}
      <div className="dialog-actions">
        <Button autoFocus onClick={close} disabled={busy}>
          取消
        </Button>
        <Button
          kind={danger ? "danger" : "primary"}
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await confirm();
              close();
            } catch (e) {
              setError(e instanceof Error ? e.message : "操作失败");
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "处理中…" : confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}
export function Paper({ preview }: { preview?: Preview }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!preview) return;
    const source = URL.createObjectURL(
      new Blob([preview.svg], { type: "image/svg+xml" }),
    );
    queueMicrotask(() => setUrl(source));
    return () => URL.revokeObjectURL(source);
  }, [preview]);
  return (
    <div className="paper-stage">
      {preview && url ? (
        <img
          src={url}
          className="poem-paper"
          alt={`诗词纸张排版预览，${preview.cells} 个字位`}
        />
      ) : (
        <span className="muted">等待排版预览</span>
      )}
    </div>
  );
}
export function PreviewNotes({ preview }: { preview?: Preview }) {
  if (!preview) return null;
  return (
    <div className="preview-notes">
      {preview.pending_gfont && (
        <Notice>最终字形加载中，完成后才能书写。</Notice>
      )}
      {preview.shrunk && (
        <Notice>
          字号由 {preview.requested_size} mm 自动缩小至 {preview.font_size_mm}{" "}
          mm，以适应纸张。
        </Notice>
      )}
      {!!preview.missing.length && (
        <Notice error>
          缺字：{preview.missing.join("、")}
          。这些字无法绘制，请更换字库或修改正文。
        </Notice>
      )}
      {preview.warnings.map((warning, i) => (
        <Notice key={i}>{warning}</Notice>
      ))}
      {preview.can_print === false && !preview.pending_gfont && (
        <Notice error>没有可绘制的字符，无法开始书写。</Notice>
      )}
    </div>
  );
}
