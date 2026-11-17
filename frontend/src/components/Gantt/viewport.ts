export interface GanttViewport {
  originDay: number;
  dayWidth: number;
  scrollX: number;
  /** Width of the time canvas, excluding the vertical scrollbar. */
  width: number;
}

/** Imperative bridge: horizontal scrolling never updates React/page state. */
export function createViewportChannel() {
  let current: GanttViewport | null = null;
  const listeners = new Set<() => void>();
  return {
    get: () => current,
    publish: (next: GanttViewport) => {
      if (current && current.originDay === next.originDay && current.dayWidth === next.dayWidth
        && current.scrollX === next.scrollX && current.width === next.width) return;
      current = next;
      listeners.forEach(listener => listener());
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

export type ViewportChannel = ReturnType<typeof createViewportChannel>;
