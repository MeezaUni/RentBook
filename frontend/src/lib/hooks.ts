import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { api, ApiError } from "./api";
import { currentPeriod, isValidPeriod } from "./money";

/** Fetch JSON from the API. `url = null` skips the request. */
export function useApi<T>(url: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(Boolean(url));
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!url) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const d = await api.get<T>(url);
      if (mine === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, "network", "Network error"));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, error, loading, reload: load, setData };
}

/** The selected month lives in the URL (?month=YYYY-MM) so links and reloads keep it. */
export function usePeriod(): [string, (p: string) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("month") ?? "";
  const period = isValidPeriod(raw) ? raw : currentPeriod();
  const set = useCallback(
    (p: string) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (p === currentPeriod()) next.delete("month");
        else next.set("month", p);
        return next;
      }, { replace: true });
    },
    [setParams],
  );
  return [period, set];
}
