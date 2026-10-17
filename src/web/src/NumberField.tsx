import {
  createContext,
  useContext,
  useEffect,
  useId,
  useState,
  type ReactNode,
} from "react";

type Report = (id: string, error?: string) => void;
const NumberFields = createContext<Report>(() => undefined);

export function NumberFieldsProvider({
  report,
  children,
}: {
  report: Report;
  children: ReactNode;
}) {
  return (
    <NumberFields.Provider value={report}>{children}</NumberFields.Provider>
  );
}

function validate(text: string, min: number, max: number, final = false) {
  const trimmed = text.trim();
  const partial =
    /^[+-]?(?:\d+\.)?$/.test(trimmed) || /^[+-]?\.$/.test(trimmed);
  const numeric = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(trimmed);
  if ((!final && partial) || !numeric)
    return {
      error: partial && !final ? undefined : "请输入完整的有效数值",
      invalid: true,
    };
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < min || value > max)
    return {
      error: `请输入 ${Number(min.toFixed(3))}–${Number(max.toFixed(3))} mm 范围内的数值`,
      invalid: true,
    };
  return { value, invalid: false, error: undefined };
}

export function NumberField({
  label,
  value,
  min = 0,
  max = 297,
  change,
  disabled = false,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  change: (value: number) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const report = useContext(NumberFields);
  const [draft, setDraft] = useState({
    text: String(Number(value.toFixed(3))),
    model: value,
    final: false,
  });
  // 来自拖动或其他字段的更新覆盖旧草稿；自身提交保留输入中的小数格式。
  if (draft.model !== value) {
    setDraft({
      text: String(Number(value.toFixed(3))),
      model: value,
      final: false,
    });
  }
  const checked = validate(draft.text, min, max, draft.final);
  useEffect(() => {
    report(
      id,
      checked.invalid && !disabled
        ? `${label}：${checked.error || "请完成数值输入"}`
        : undefined,
    );
  }, [report, id, label, checked.invalid, checked.error, disabled]);
  useEffect(() => () => report(id), [report, id]);

  function update(text: string, final = false) {
    const next = validate(text, min, max, final);
    report(
      id,
      next.invalid ? `${label}：${next.error || "请完成数值输入"}` : undefined,
    );
    setDraft({
      text: final && !next.invalid ? String(next.value) : text,
      model: next.value ?? value,
      final,
    });
    if (next.value !== undefined) change(next.value);
  }
  return (
    <label className="compact-field">
      <span>{label}</span>
      <div className="dimension-value">
        <input
          aria-label={label}
          type="text"
          inputMode="decimal"
          data-number-field="true"
          aria-invalid={checked.invalid}
          aria-describedby={checked.error ? `${id}-error` : undefined}
          value={draft.text}
          disabled={disabled}
          onChange={(event) => update(event.target.value)}
          onBlur={() => update(draft.text, true)}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Enter") {
              event.preventDefault();
              event.stopPropagation();
              update(draft.text, true);
            } else if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              report(id);
              setDraft({
                text: String(Number(value.toFixed(3))),
                model: value,
                final: false,
              });
            }
          }}
        />
        <span>mm</span>
      </div>
      {checked.error && (
        <small className="number-field-error" id={`${id}-error`}>
          {checked.error}
        </small>
      )}
    </label>
  );
}
