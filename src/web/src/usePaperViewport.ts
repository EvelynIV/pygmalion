import { useEffect, useLayoutEffect, useRef, useState } from "react";

export const PIXELS_PER_MM = 96 / 25.4;
const MIN_SCALE = PIXELS_PER_MM * 0.1;
const MAX_SCALE = PIXELS_PER_MM * 4;

/** 纸面毫米坐标到视口像素的唯一映射；缩放只修改视图。 */
export function usePaperViewport(width: number, height: number) {
  const stage = useRef<HTMLDivElement>(null);
  const paper = useRef<HTMLDivElement>(null);
  const [hand, setHand] = useState(false);
  const [panning, setPanning] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const panPosition = useRef(pan);
  useLayoutEffect(() => {
    panPosition.current = pan;
  }, [pan]);
  const [zoom, setZoom] = useState("fit");
  const [scale, setScale] = useState(2);
  const [view, setView] = useState({ x: 0, y: 0, width: 0, height: 0 });
  const [cursor, setCursor] = useState<{ x: number; y: number }>();
  const anchor = useRef<
    { x: number; y: number; clientX: number; clientY: number } | undefined
  >(undefined);
  const clientPoint = useRef<{ x: number; y: number } | undefined>(undefined);
  const liveScale = useRef(scale);
  useLayoutEffect(() => {
    liveScale.current = scale;
  }, [scale]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (
        event.isComposing ||
        event.keyCode === 229 ||
        event.repeat ||
        document.querySelector('[role="dialog"]')
      )
        return;
      if (event.key === "Escape" && hand) {
        event.preventDefault();
        event.stopPropagation();
        setHand(false);
        return;
      }
      const target = event.target instanceof Element ? event.target : null;
      if (
        target?.closest(
          'input,textarea,select,[contenteditable]:not([contenteditable="false"])',
        )
      )
        return;
      if (
        event.key.toLowerCase() === "h" &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey
      ) {
        event.preventDefault();
        setHand(true);
      }
    };
    document.addEventListener("keydown", key, true);
    return () => document.removeEventListener("keydown", key, true);
  }, [hand]);

  useEffect(() => {
    const element = stage.current;
    if (!element || !hand) return;
    let gesture:
      | { id: number; x: number; y: number; left: number; top: number }
      | undefined;
    const down = (event: PointerEvent) => {
      if (event.button !== 0 || gesture) return;
      event.preventDefault();
      event.stopPropagation();
      gesture = {
        id: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        left: panPosition.current.x,
        top: panPosition.current.y,
      };
      element.setPointerCapture(event.pointerId);
      setPanning(true);
    };
    const move = (event: PointerEvent) => {
      if (!gesture || gesture.id !== event.pointerId) return;
      event.preventDefault();
      event.stopPropagation();
      clientPoint.current = { x: event.clientX, y: event.clientY };
      setPan({
        x: gesture.left + event.clientX - gesture.x,
        y: gesture.top + event.clientY - gesture.y,
      });
    };
    const end = () => {
      const old = gesture;
      gesture = undefined;
      setPanning(false);
      if (old && element.hasPointerCapture(old.id))
        element.releasePointerCapture(old.id);
    };
    const up = (event: PointerEvent) => {
      if (!gesture || gesture.id !== event.pointerId) return;
      event.preventDefault();
      event.stopPropagation();
      end();
    };
    const suppress = (event: Event) => {
      event.preventDefault();
      event.stopPropagation();
    };
    element.addEventListener("pointerdown", down, true);
    element.addEventListener("pointermove", move, true);
    element.addEventListener("pointerup", up, true);
    element.addEventListener("pointercancel", up, true);
    element.addEventListener("lostpointercapture", end);
    element.addEventListener("click", suppress, true);
    element.addEventListener("dblclick", suppress, true);
    window.addEventListener("blur", end);
    return () => {
      element.removeEventListener("pointerdown", down, true);
      element.removeEventListener("pointermove", move, true);
      element.removeEventListener("pointerup", up, true);
      element.removeEventListener("pointercancel", up, true);
      element.removeEventListener("lostpointercapture", end);
      element.removeEventListener("click", suppress, true);
      element.removeEventListener("dblclick", suppress, true);
      window.removeEventListener("blur", end);
      end();
    };
  }, [hand]);

  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const resize = () =>
      setScale(
        zoom === "fit"
          ? Math.max(
              MIN_SCALE,
              Math.min(
                MAX_SCALE,
                (element.clientWidth - 32) / width,
                (element.clientHeight - 32) / height,
              ),
            )
          : Math.max(
              MIN_SCALE,
              Math.min(MAX_SCALE, Number(zoom) * PIXELS_PER_MM),
            ),
      );
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    return () => observer.disconnect();
  }, [width, height, zoom]);

  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const sheet = paper.current;
      if (!sheet || !event.deltaY) return;
      const rect = sheet.getBoundingClientRect();
      const renderedScale = rect.width / width;
      anchor.current = {
        x: (event.clientX - rect.left) / renderedScale,
        y: (event.clientY - rect.top) / renderedScale,
        clientX: event.clientX,
        clientY: event.clientY,
      };
      const delta =
        event.deltaY *
        (event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? element.clientHeight
            : 1);
      const next = Math.max(
        MIN_SCALE,
        Math.min(MAX_SCALE, liveScale.current * Math.exp(-delta * 0.002)),
      );
      liveScale.current = next;
      setZoom(String(next / PIXELS_PER_MM));
    };
    const target = element.parentElement || element;
    target.addEventListener("wheel", wheel, { passive: false });
    return () => target.removeEventListener("wheel", wheel);
  }, [width]);

  useLayoutEffect(() => {
    const element = stage.current,
      sheet = paper.current;
    if (!element || !sheet) return;
    if (anchor.current) {
      const at = anchor.current,
        rect = sheet.getBoundingClientRect();
      element.scrollLeft += rect.left + at.x * scale - at.clientX;
      element.scrollTop += rect.top + at.y * scale - at.clientY;
      anchor.current = undefined;
    }
    let frame = 0;
    const update = () => {
      const rect = sheet.getBoundingClientRect(),
        viewport = element.getBoundingClientRect();
      if (clientPoint.current) {
        const x = (clientPoint.current.x - rect.left) / scale,
          y = (clientPoint.current.y - rect.top) / scale;
        setCursor(
          x >= 0 && y >= 0 && x <= width && y <= height ? { x, y } : undefined,
        );
      }
      setView({
        x: rect.left - viewport.left,
        y: rect.top - viewport.top,
        width: element.clientWidth,
        height: element.clientHeight,
      });
    };
    const scroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(update);
    };
    const observer = new ResizeObserver(scroll);
    observer.observe(element);
    element.addEventListener("scroll", scroll, { passive: true });
    update();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      element.removeEventListener("scroll", scroll);
    };
  }, [scale, width, height, pan]);

  function point(clientX: number, clientY: number) {
    clientPoint.current = { x: clientX, y: clientY };
    const rect = paper.current?.getBoundingClientRect();
    if (!rect) return;
    const x = (clientX - rect.left) / scale,
      y = (clientY - rect.top) / scale;
    setCursor(
      x >= 0 && y >= 0 && x <= width && y <= height ? { x, y } : undefined,
    );
  }
  return {
    stage,
    paper,
    hand,
    setHand,
    panning,
    pan,
    zoom,
    setZoom,
    scale,
    view,
    cursor,
    point,
    clearCursor: () => {
      clientPoint.current = undefined;
      setCursor(undefined);
    },
  };
}
