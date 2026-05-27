import { useEffect, useRef, useState } from "react";

// Run `loader` once on mount, then every `intervalMs`. Pauses while the
// page is hidden so the dashboard doesn't keep hammering the API in a
// background tab. Exposes a `refresh` function to trigger an immediate
// reload outside the timer.
export function usePolling<T>(
  loader: () => Promise<T>,
  intervalMs: number,
): { data: T | null; error: Error | null; loading: boolean; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const tick = async () => {
      try {
        const next = await loaderRef.current();
        if (!cancelled) {
          setData(next);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    const start = () => {
      tick();
      timer = setInterval(tick, intervalMs);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };

    if (!document.hidden) start();
    const onVis = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVis);

    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [intervalMs]);

  const refresh = () => {
    loaderRef.current().then((next) => setData(next)).catch((e) => {
      setError(e instanceof Error ? e : new Error(String(e)));
    });
  };

  return { data, error, loading, refresh };
}
