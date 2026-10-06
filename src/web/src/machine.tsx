import { useEffect, useRef, useState, type PointerEvent } from "react";
import {
  api,
  useResource,
  type Calibration,
  type Field,
  type Machine,
  type Ports,
} from "./api";
import { Button, Dialog, Heading, Notice, ResourceState } from "./ui";
import { ControlError, MachineControl } from "./control";

type Notify = (text: string, error?: boolean) => void;

export function MachinePage({
  busy,
  notify,
}: {
  busy: boolean;
  notify: Notify;
}) {
  const ports = useResource<Ports>("/api/admin/serial-ports");
  const [port, setPort] = useState("");
  const [machine, setMachine] = useState<Machine>();
  const [machineError, setMachineError] = useState("");
  const [reading, setReading] = useState(false);
  const [step, setStep] = useState(5);
  const [working, setWorking] = useState(false);
  const [zero, setZero] = useState(false);
  const [connectionExpanded, setConnectionExpanded] = useState(true);
  const [jogCount, setJogCount] = useState(0);
  const pendingJogs = useRef(0);
  const operating = useRef(false);
  const mounted = useRef(true);
  const busyRef = useRef(busy);
  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);
  const [control] = useState(
    () =>
      new MachineControl(
        (state) => {
          setMachine((previous) =>
            previous ? { ...previous, state } : previous,
          );
        },
        (message) => {
          setMachineError(message);
        },
        () => {
          setMachine(undefined);
        },
      ),
  );
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      control.close(false);
    };
  }, [control]);
  const connected = !!machine && !machineError;
  const available = connected && !busy && !working && jogCount === 0;
  const jogAvailable = connected && !busy && !working && jogCount < 4;
  function updatePosition(result: {
    state?: Record<string, string>;
    backup?: string;
  }) {
    setMachine((previous) =>
      previous
        ? {
            ...previous,
            state: result.state || previous.state,
            backup: result.backup || previous.backup,
          }
        : previous,
    );
  }
  async function action(
    path: string,
    payload?: object,
    label = "操作完成",
    propagate = false,
  ) {
    if (operating.current || pendingJogs.current || busyRef.current) {
      if (propagate) throw new Error("请等待当前动作完成");
      return;
    }
    operating.current = true;
    setWorking(true);
    if (path === "/api/admin/machine") setReading(true);
    try {
      type Result = {
        path?: string;
        port?: string;
        after?: string;
        state?: Record<string, string>;
        backup?: string;
      };
      const kinds: Record<string, string> = {
        "/api/admin/machine": "inspect",
        "/api/admin/jog": "jog",
        "/api/admin/backup": "backup",
        "/api/admin/recover": "recover",
        "/api/admin/machine/disconnect": "disconnect",
      };
      let result: Result;
      if (kinds[path]) {
        result = await control.request<Result>(kinds[path], payload);
      } else {
        await control.disconnect();
        result = await api<Result>(path, payload);
      }
      if (!mounted.current) return;
      if (path === "/api/admin/machine") {
        setMachine(result as Machine);
        setMachineError("");
      } else if (
        path === "/api/admin/serial-port" ||
        path === "/api/admin/serial-ports/detect"
      ) {
        setMachine(undefined);
        setMachineError("");
        setPort(result.port || "");
        ports.reload();
      } else {
        updatePosition(result);
        if (path === "/api/admin/recover") {
          setMachine(await control.request<Machine>("inspect"));
          setMachineError("");
        }
      }
      notify(
        `${label}${result.path ? `：${result.path}` : result.after ? `：${result.after}` : result.port ? `：${result.port}` : ""}`,
      );
    } catch (error) {
      if (!mounted.current) return;
      const message = error instanceof Error ? error.message : "设备操作失败";
      if (path === "/api/admin/machine" || path === "/api/admin/jog")
        setMachineError(message);
      notify(message, true);
      if (propagate) throw error;
    } finally {
      operating.current = false;
      if (mounted.current) {
        setWorking(false);
        setReading(false);
      }
    }
  }
  async function enqueueJog(axis: "x" | "y", distance: number) {
    if (
      !connected ||
      operating.current ||
      busyRef.current ||
      !mounted.current ||
      pendingJogs.current >= 4
    )
      return;
    pendingJogs.current += 1;
    setJogCount(pendingJogs.current);
    try {
      const result = await control.request<{
        state: Record<string, string>;
        backup: string;
      }>("jog", { action: axis, dist: distance });
      if (mounted.current) updatePosition(result);
    } catch (error) {
      if (
        mounted.current &&
        !(error instanceof ControlError && error.cancelled)
      ) {
        const message = error instanceof Error ? error.message : "点动失败";
        setMachineError(message);
        notify(`${message}；待执行点动已取消，请重新读取设备状态`, true);
      }
    } finally {
      pendingJogs.current -= 1;
      if (mounted.current) setJogCount(pendingJogs.current);
    }
  }
  const position = machine?.state.WPos || machine?.state.MPos;
  const axes = position?.split(",");
  return (
    <>
      <Heading title="机器控制">
        检测设备，将笔尖对到刻度区域左上角，再设置工作原点。
      </Heading>
      {busy && <Notice>机器正在书写，手动控制和串口扫描暂不可用。</Notice>}
      <details
        className="connection-drawer"
        open={connectionExpanded}
        onToggle={(event) => setConnectionExpanded(event.currentTarget.open)}
      >
        <summary>
          连接信息 ·{" "}
          {machineError ? "设备不可用" : machine?.state.state || "尚未读取"} ·{" "}
          {machine?.port || ports.data?.configured_port || "—"}
        </summary>
        <ResourceState
          loading={ports.loading}
          error={ports.error}
          retry={ports.reload}
        />
        <div className="inline-fields">
          <label className="field">
            <span>串口</span>
            <select
              value={port || ports.data?.configured_port || "auto"}
              onChange={(event) => setPort(event.target.value)}
            >
              <option value="auto">自动识别 GRBL</option>
              {ports.data?.ports.map((item) => (
                <option key={item.device} value={item.device}>
                  {item.device} · {item.description}
                </option>
              ))}
            </select>
          </label>
          <Button
            disabled={busy || working || jogCount > 0}
            onClick={() =>
              void action(
                "/api/admin/serial-port",
                { port: port || ports.data?.configured_port || "auto" },
                "串口已保存",
              )
            }
          >
            保存串口
          </Button>
          <Button
            disabled={busy || working || jogCount > 0}
            onClick={() =>
              void action("/api/admin/serial-ports/detect", {}, "设备已检测")
            }
          >
            检测串口
          </Button>
          <Button
            disabled={busy || working || jogCount > 0}
            onClick={() =>
              void action("/api/admin/machine", undefined, "设备状态已读取")
            }
          >
            读取设备状态
          </Button>
          <Button
            disabled={!machine || busy || working || jogCount > 0}
            onClick={() =>
              void action(
                "/api/admin/machine/disconnect",
                undefined,
                "设备连接已断开",
              )
            }
          >
            断开连接
          </Button>
        </div>
        <ResourceState
          loading={reading}
          error={machineError}
          retry={() =>
            void action("/api/admin/machine", undefined, "设备状态已读取")
          }
        />
        <dl className="device-facts">
          <div>
            <dt>设备状态</dt>
            <dd>
              {machineError ? "设备不可用" : machine?.state.state || "尚未读取"}
            </dd>
          </div>
          <div>
            <dt>连接</dt>
            <dd>
              {machine?.port || ports.data?.configured_port || "—"} /{" "}
              {ports.data?.baud || "—"}
            </dd>
          </div>
          <div>
            <dt>固件</dt>
            <dd>{machine?.firmware.join(" · ") || "—"}</dd>
          </div>
          <div>
            <dt>最近备份</dt>
            <dd title={machine?.backup || ""}>
              {machine?.backup || "暂无备份"}
            </dd>
          </div>
        </dl>
      </details>
      <div className="machine-workspace">
        <section className="tool-section">
          <h2>点动</h2>
          <p className="muted">
            以下操作直接控制真实设备，移动前检查当天备份。连续点动按点击顺序执行。
          </p>
          <div className="coordinates">
            {["X", "Y", "Z"].map((axis, index) => (
              <div key={axis}>
                <span>{axis}</span>
                <strong>{machineError ? "—" : axes?.[index] || "—"}</strong>
                <small>mm</small>
              </div>
            ))}
          </div>
          <label className="field step-select">
            <span>单次移动</span>
            <select
              value={step}
              onChange={(event) => setStep(Number(event.target.value))}
            >
              {[1, 5, 10, 30].map((value) => (
                <option key={value} value={value}>
                  {value} mm
                </option>
              ))}
            </select>
          </label>
          <div className="jog-pad">
            <Button
              className="jog-up"
              disabled={!jogAvailable}
              aria-label={`Y 正向移动 ${step} 毫米`}
              onClick={() => enqueueJog("y", step)}
            >
              ↑
            </Button>
            <Button
              className="jog-left"
              disabled={!jogAvailable}
              aria-label={`X 负向移动 ${step} 毫米`}
              onClick={() => enqueueJog("x", -step)}
            >
              ←
            </Button>
            <span>X / Y</span>
            <Button
              className="jog-right"
              disabled={!jogAvailable}
              aria-label={`X 正向移动 ${step} 毫米`}
              onClick={() => enqueueJog("x", step)}
            >
              →
            </Button>
            <Button
              className="jog-down"
              disabled={!jogAvailable}
              aria-label={`Y 负向移动 ${step} 毫米`}
              onClick={() => enqueueJog("y", -step)}
            >
              ↓
            </Button>
          </div>
          <p className="muted" role="status">
            {jogCount
              ? `正在点动 · 等待 ${jogCount - 1} 次`
              : "可连续点击方向，最多等待 3 次点动。"}
          </p>
          {jogCount > 1 && (
            <Button
              onClick={() => {
                void control
                  .request("cancel_pending")
                  .catch((error: Error) => notify(error.message, true));
              }}
            >
              取消待执行点动
            </Button>
          )}
          <div className="action-row">
            <Button
              disabled={!available}
              onClick={() =>
                void action("/api/admin/jog", { action: "pen_up" }, "抬笔完成")
              }
            >
              抬笔
            </Button>
            <Button
              disabled={!available}
              onClick={() =>
                void action(
                  "/api/admin/jog",
                  { action: "pen_down" },
                  "落笔完成",
                )
              }
            >
              落笔
            </Button>
            <Button disabled={!available} onClick={() => setZero(true)}>
              设为原点
            </Button>
            <Button
              disabled={!available}
              onClick={() =>
                void action(
                  "/api/admin/jog",
                  { action: "goto_zero" },
                  "已移回原点",
                )
              }
            >
              移回原点
            </Button>
          </div>
        </section>
        <section className="tool-section">
          <h2>机器维护</h2>
          <p>
            备份包含控制板参数、坐标和工作模式。恢复解除 Hold / Alarm
            并抬笔，不会自动续写。
          </p>
          <div className="action-row">
            <Button
              disabled={busy || working || jogCount > 0}
              onClick={() =>
                void action("/api/admin/backup", undefined, "机器设置已备份")
              }
            >
              备份机器设置
            </Button>
            <Button
              disabled={busy || working || jogCount > 0}
              onClick={() => void action("/api/admin/recover", {}, "恢复结果")}
            >
              解除卡死 / 恢复
            </Button>
          </div>
          <p className="muted">备份参数可在参数设置中预览、确认后写回。</p>
        </section>
      </div>
      {zero && (
        <Dialog
          title="设置工作原点"
          confirmLabel="设为原点"
          close={() => setZero(false)}
          confirm={async () => {
            await action(
              "/api/admin/jog",
              { action: "zero" },
              "当前位置已设为工作原点",
              true,
            );
          }}
        >
          确认笔尖位于刻度区域左上角。本操作将当前位置设置为 X0 / Y0。
        </Dialog>
      )}
    </>
  );
}

export function CalibrationPage({ notify }: { notify: Notify }) {
  const resource = useResource<Calibration>("/api/admin/grid-calibration");
  return (
    <>
      <Heading title="纸张校准">
        调整纸张相对刻度区原点的位置。保存不会移动机器。
      </Heading>
      <ResourceState
        loading={resource.loading}
        error={resource.error}
        retry={resource.reload}
      />
      {resource.data && (
        <CalibrationEditor
          key={JSON.stringify(resource.data)}
          calibration={resource.data}
          reload={resource.reload}
          notify={notify}
        />
      )}
    </>
  );
}
function CalibrationEditor({
  calibration,
  reload,
  notify,
}: {
  calibration: Calibration;
  reload: () => void;
  notify: Notify;
}) {
  const [form, setForm] = useState({
    grid_width_mm: calibration.grid_mm[0],
    grid_height_mm: calibration.grid_mm[1],
    paper_width_mm: calibration.paper_mm[0],
    paper_height_mm: calibration.paper_mm[1],
    paper_offset_x_mm: calibration.paper_offset_mm[0],
    paper_offset_y_mm: calibration.paper_offset_mm[1],
    paper_preset: calibration.paper_preset,
  });
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [drag, setDrag] = useState<{
    clientX: number;
    clientY: number;
    x: number;
    y: number;
    scale: number;
  }>();
  const [minX, minY] = calibration.paper_bounds_mm;
  const spanX = form.grid_width_mm - minX;
  const spanY = form.grid_height_mm - minY;
  const valid =
    Object.values(form).every(
      (value) => typeof value === "string" || Number.isFinite(value),
    ) &&
    form.grid_width_mm >= 50 &&
    form.grid_width_mm <= 400 &&
    form.grid_height_mm >= 50 &&
    form.grid_height_mm <= 400 &&
    form.paper_width_mm >= 20 &&
    form.paper_height_mm >= 20 &&
    form.paper_offset_x_mm >= minX &&
    form.paper_offset_y_mm >= minY &&
    form.paper_offset_x_mm + form.paper_width_mm <=
      form.grid_width_mm + 0.001 &&
    form.paper_offset_y_mm + form.paper_height_mm <=
      form.grid_height_mm + 0.001;
  function change(key: string, value: number | string) {
    setForm((old) => ({ ...old, [key]: value }));
    setDirty(true);
  }
  function move(event: PointerEvent<SVGRectElement>) {
    if (!drag) return;
    setForm((old) => ({
      ...old,
      paper_offset_x_mm:
        Math.round(
          (drag.x + (event.clientX - drag.clientX) / drag.scale) * 10,
        ) / 10,
      paper_offset_y_mm:
        Math.round(
          (drag.y + (event.clientY - drag.clientY) / drag.scale) * 10,
        ) / 10,
    }));
    setDirty(true);
  }
  async function save() {
    setSaving(true);
    try {
      await api("/api/admin/grid-calibration", { values: form });
      setDirty(false);
      notify("纸张位置已保存");
      reload();
    } catch (error) {
      notify(error instanceof Error ? error.message : "保存失败", true);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="calibration-workspace">
      <section>
        <div className="calibration-stage">
          <svg
            className="calibration-canvas"
            viewBox={`${minX} ${minY} ${spanX} ${spanY}`}
            aria-label="纸张与写字盘位置示意图"
          >
            <defs>
              <pattern
                id="grid"
                width="10"
                height="10"
                patternUnits="userSpaceOnUse"
              >
                <path
                  d="M 10 0 L 0 0 0 10"
                  fill="none"
                  className="grid-line"
                  vectorEffect="non-scaling-stroke"
                />
              </pattern>
            </defs>
            <rect
              x={minX}
              y={minY}
              width={spanX}
              height={spanY}
              fill="url(#grid)"
            />
            <rect
              x="0"
              y="0"
              width={form.grid_width_mm}
              height={form.grid_height_mm}
              className="scale-area"
              vectorEffect="non-scaling-stroke"
            />
            <rect
              x={form.paper_offset_x_mm}
              y={form.paper_offset_y_mm}
              width={form.paper_width_mm}
              height={form.paper_height_mm}
              className={`calibration-paper ${valid ? "" : "invalid"}`}
              vectorEffect="non-scaling-stroke"
              tabIndex={0}
              role="group"
              aria-label="纸张位置；拖动或使用方向键移动一毫米"
              onPointerDown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId);
                const bounds =
                  event.currentTarget.ownerSVGElement!.getBoundingClientRect();
                setDrag({
                  clientX: event.clientX,
                  clientY: event.clientY,
                  x: form.paper_offset_x_mm,
                  y: form.paper_offset_y_mm,
                  scale: Math.min(bounds.width / spanX, bounds.height / spanY),
                });
              }}
              onPointerMove={move}
              onPointerUp={() => setDrag(undefined)}
              onPointerCancel={() => setDrag(undefined)}
              onKeyDown={(event) => {
                const offsets: Record<string, [number, number]> = {
                  ArrowLeft: [-1, 0],
                  ArrowRight: [1, 0],
                  ArrowUp: [0, -1],
                  ArrowDown: [0, 1],
                };
                if (offsets[event.key]) {
                  event.preventDefault();
                  const [x, y] = offsets[event.key];
                  setForm((old) => ({
                    ...old,
                    paper_offset_x_mm: old.paper_offset_x_mm + x,
                    paper_offset_y_mm: old.paper_offset_y_mm + y,
                  }));
                  setDirty(true);
                }
              }}
            />
            <path
              d="M-5 0H5M0-5V5"
              className="origin-cross"
              vectorEffect="non-scaling-stroke"
              pointerEvents="none"
            />
          </svg>
        </div>
        <p className="muted">
          {form.paper_width_mm} × {form.paper_height_mm} mm · X{" "}
          {form.paper_offset_x_mm} / Y {form.paper_offset_y_mm} mm
        </p>
        <p>
          拖动纸张或输入数值。允许左移最多 100 mm、上移最多 50
          mm；右边和下边不能越过刻度区边界。
        </p>
      </section>
      <section className="inspector">
        <h2>纸张位置 {dirty && <small className="muted">未保存</small>}</h2>
        <label className="field">
          <span>纸张规格</span>
          <select
            value={form.paper_preset}
            onChange={(event) => {
              const preset = calibration.paper_presets.find(
                (item) => item.id === event.target.value,
              );
              setForm((old) => ({
                ...old,
                paper_preset: event.target.value,
                ...(preset
                  ? {
                      paper_width_mm: preset.width_mm,
                      paper_height_mm: preset.height_mm,
                    }
                  : {}),
              }));
              setDirty(true);
            }}
          >
            <option value="custom">自定义</option>
            {calibration.paper_presets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.width_mm} × {item.height_mm} mm
              </option>
            ))}
          </select>
        </label>
        <div className="two-fields">
          {(
            [
              ["grid_width_mm", "刻度区横向", 50, 400],
              ["grid_height_mm", "刻度区竖向", 50, 400],
              ["paper_width_mm", "纸张横向", 20, 1500],
              ["paper_height_mm", "纸张竖向", 20, 1500],
              ["paper_offset_x_mm", "纸张偏移 X", -100, 400],
              ["paper_offset_y_mm", "纸张偏移 Y", -50, 400],
            ] as const
          ).map(([key, label, min, max]) => (
            <label className="field" key={key}>
              <span>
                {label}
                <small>mm</small>
              </span>
              <input
                type="number"
                step="0.1"
                min={min}
                max={max}
                value={form[key]}
                onChange={(event) => change(key, Number(event.target.value))}
              />
            </label>
          ))}
        </div>
        <Notice error={!valid}>
          {valid
            ? "纸张位置有效，完整位于可书写范围。"
            : "校准越界：请调整纸张尺寸或偏移后保存。"}
        </Notice>
        <Button
          className="full-width"
          kind="primary"
          disabled={!valid || !dirty || saving}
          onClick={() => void save()}
        >
          {saving ? "正在保存…" : "保存纸张位置"}
        </Button>
        <Button className="full-width" disabled={saving} onClick={reload}>
          放弃修改 / 重新读取
        </Button>
      </section>
    </div>
  );
}

export function SettingsPage({
  busy,
  notify,
}: {
  busy: boolean;
  notify: Notify;
}) {
  const config = useResource<{ fields: Field[] }>("/api/admin/config");
  return (
    <>
      <Heading title="参数设置">
        软件参数独立保存；高级 GRBL 参数预览后确认写入。
      </Heading>
      <ResourceState
        loading={config.loading}
        error={config.error}
        retry={config.reload}
      />
      {config.data && (
        <SettingsEditor
          key={JSON.stringify(config.data)}
          fields={config.data.fields}
          busy={busy}
          reload={config.reload}
          notify={notify}
        />
      )}
    </>
  );
}
function SettingsEditor({
  fields,
  busy,
  reload,
  notify,
}: {
  fields: Field[];
  busy: boolean;
  reload: () => void;
  notify: Notify;
}) {
  const [values, setValues] = useState<Record<string, number>>(() =>
    Object.fromEntries(fields.map((field) => [field.key, field.value])),
  );
  const [saving, setSaving] = useState(false);
  const [parameter, setParameter] = useState("$110");
  const [target, setTarget] = useState("4000");
  const [preview, setPreview] = useState<{
    commands: string[];
    changes: Record<string, string>;
  }>();
  async function save() {
    setSaving(true);
    try {
      const result = await api<{ clamped: string[]; rejected: string[] }>(
        "/api/admin/config",
        { values },
      );
      notify(
        `软件参数已保存${result.clamped.length ? `；已调整：${result.clamped.join("、")}` : ""}${result.rejected.length ? `；未保存：${result.rejected.join("、")}` : ""}`,
      );
      reload();
    } catch (error) {
      notify(error instanceof Error ? error.message : "保存失败", true);
    } finally {
      setSaving(false);
    }
  }
  async function previewWrite() {
    setSaving(true);
    try {
      const changes = { [parameter]: target };
      const result = await api<{ will_send: string[] }>("/api/admin/settings", {
        changes,
        confirm: false,
      });
      setPreview({ commands: result.will_send, changes });
    } catch (error) {
      notify(error instanceof Error ? error.message : "预览失败", true);
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <section className="tool-section">
        <h2>软件书写参数</h2>
        <p className="muted">
          保存到本地配置，不写入控制板。已入队任务保留原参数。
        </p>
        <div className="settings-grid">
          {fields.map((field) => (
            <label className="field" key={field.key}>
              <span>
                {field.label}
                <small>{field.unit}</small>
              </span>
              <input
                aria-label={field.label}
                aria-describedby={`help-${field.key}`}
                type="number"
                value={values[field.key]}
                min={field.min}
                max={field.max}
                step={field.step}
                onChange={(event) =>
                  setValues((old) => ({
                    ...old,
                    [field.key]: Number(event.target.value),
                  }))
                }
              />
              <small id={`help-${field.key}`}>{field.help}</small>
            </label>
          ))}
        </div>
        <div className="action-row">
          <Button kind="primary" disabled={saving} onClick={() => void save()}>
            {saving ? "正在保存…" : "保存软件参数"}
          </Button>
          <Button disabled={saving} onClick={reload}>
            重新读取
          </Button>
        </div>
      </section>
      <section className="tool-section">
        <h2>高级 GRBL 参数</h2>
        <p>预览待发送指令，确认时先备份，再写入控制板。</p>
        <div className="inline-fields">
          <label className="field">
            <span>参数编号</span>
            <input
              value={parameter}
              pattern="\$\d{1,3}"
              onChange={(event) => setParameter(event.target.value)}
            />
          </label>
          <label className="field">
            <span>目标值</span>
            <input
              type="number"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            />
          </label>
          <Button
            disabled={
              busy ||
              saving ||
              !/^\$\d{1,3}$/.test(parameter) ||
              !target ||
              !Number.isFinite(Number(target))
            }
            onClick={() => void previewWrite()}
          >
            预览写入
          </Button>
          <Button
            disabled={busy || saving}
            onClick={async () => {
              setSaving(true);
              try {
                const result = await api<{ path: string }>("/api/admin/backup");
                notify(`机器设置已备份：${result.path}`);
              } catch (error) {
                notify(
                  error instanceof Error ? error.message : "备份失败",
                  true,
                );
              } finally {
                setSaving(false);
              }
            }}
          >
            备份机器设置
          </Button>
        </div>
        {busy && <Notice>书写中不能读取或修改控制板参数。</Notice>}
        <p className="muted">
          恢复备份时，将备份中的参数编号与值逐项填入，预览后确认写回。
        </p>
      </section>
      {preview && (
        <Dialog
          title="确认写入控制板"
          confirmLabel="备份并确认写入"
          danger
          close={() => setPreview(undefined)}
          confirm={async () => {
            const result = await api<{ backup: string; result: string[] }>(
              "/api/admin/settings",
              { changes: preview.changes, confirm: true },
            );
            notify(`参数已写入；备份：${result.backup}`);
          }}
        >
          <p>将执行以下指令。写入前会创建完整机器备份；备份失败时终止写入。</p>
          <pre>{preview.commands.join("\n")}</pre>
        </Dialog>
      )}
    </>
  );
}
