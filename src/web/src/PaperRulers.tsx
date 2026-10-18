const RULER_SIZE = 32;

export function PaperRulers({
  width,
  height,
  scale,
  view,
  cursor,
}: {
  width: number;
  height: number;
  scale: number;
  view: { x: number; y: number; width: number; height: number };
  cursor?: { x: number; y: number };
}) {
  const minor = scale >= 2.5 ? 1 : scale >= 0.7 ? 5 : 10;
  const labels = [10, 20, 50, 100].find((step) => step * scale >= 36) || 100;
  function ticks(length: number, offset: number, vertical: boolean) {
    return Array.from(
      { length: Math.floor(length / minor) + 1 },
      (_, i) => i * minor,
    ).map((mm) => {
      const pos = offset + mm * scale,
        major = mm % labels === 0;
      if (pos < -1 || pos > (vertical ? view.height : view.width) + 1)
        return null;
      const tick = major ? 10 : mm % 5 === 0 ? 7 : 4;
      return (
        <g key={mm} data-mm={mm}>
          {vertical ? (
            <line x1={RULER_SIZE - tick} y1={pos} x2={RULER_SIZE} y2={pos} />
          ) : (
            <line x1={pos} y1={RULER_SIZE - tick} x2={pos} y2={RULER_SIZE} />
          )}
          {major &&
            (vertical ? (
              <text x="2" y={pos + 3}>
                {mm}
              </text>
            ) : (
              <text x={pos + 3} y="12">
                {mm}
              </text>
            ))}
        </g>
      );
    });
  }
  return (
    <>
      <span className="ruler-corner" aria-label="刻度单位：毫米">
        mm
      </span>
      <svg
        className="paper-ruler horizontal"
        aria-label="水平毫米刻度尺"
        width={Math.max(1, view.width)}
        height={RULER_SIZE}
        viewBox={`0 0 ${Math.max(1, view.width)} ${RULER_SIZE}`}
        preserveAspectRatio="none"
      >
        {ticks(width, view.x, false)}
        {cursor && (
          <line
            className="ruler-cursor"
            x1={view.x + cursor.x * scale}
            x2={view.x + cursor.x * scale}
            y1="0"
            y2={RULER_SIZE}
          />
        )}
      </svg>
      <svg
        className="paper-ruler vertical"
        aria-label="垂直毫米刻度尺"
        width={RULER_SIZE}
        height={Math.max(1, view.height)}
        viewBox={`0 0 ${RULER_SIZE} ${Math.max(1, view.height)}`}
        preserveAspectRatio="none"
      >
        {ticks(height, view.y, true)}
        {cursor && (
          <line
            className="ruler-cursor"
            x1="0"
            x2={RULER_SIZE}
            y1={view.y + cursor.y * scale}
            y2={view.y + cursor.y * scale}
          />
        )}
      </svg>
    </>
  );
}
