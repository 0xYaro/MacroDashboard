"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ManualPosition, WatchTicker } from "@/lib/contract";
import {
  fetchManualPositions,
  fetchWatchTickers,
  keys,
  type ManualPositionInput,
  type WatchTickerInput,
  type WriteResult,
} from "@/lib/data";
import { useSource } from "./useSource";

/**
 * The operator's two short holdings lists and the four writes that edit them.
 *
 * Read only while the editor is open (`enabled`), since the page itself shows
 * the holdings watch, not the lists. Each write settles by re-reading its
 * list, never by writing the answer into the cache, so the row the operator
 * sees next is the row the database holds, including a refusal the function
 * raised.
 */
export function useHoldingLists(enabled: boolean) {
  const { source } = useSource();
  const client = useQueryClient();
  const scope = source?.scope ?? "none";
  const on = enabled && source !== null;

  const manual = useQuery({
    enabled: on,
    retry: 1,
    queryKey: keys.manualPositions(scope),
    queryFn: () => fetchManualPositions(source!),
  });
  const watch = useQuery({
    enabled: on,
    retry: 1,
    queryKey: keys.watchTickers(scope),
    queryFn: () => fetchWatchTickers(source!),
  });

  const none = async (): Promise<WriteResult> => ({
    ok: false,
    reason: "No data source is configured.",
  });
  const settleManual = () =>
    client.invalidateQueries({ queryKey: keys.manualPositions(scope) });
  const settleWatch = () =>
    client.invalidateQueries({ queryKey: keys.watchTickers(scope) });

  const saveManual = useMutation<WriteResult, Error, ManualPositionInput>({
    mutationFn: (input) =>
      source === null ? none() : source.saveManualPosition(input),
    onSettled: settleManual,
  });
  const retireManual = useMutation<WriteResult, Error, string>({
    mutationFn: (id) =>
      source === null ? none() : source.retireManualPosition(id),
    onSettled: settleManual,
  });
  const saveWatch = useMutation<WriteResult, Error, WatchTickerInput>({
    mutationFn: (input) =>
      source === null ? none() : source.saveWatchTicker(input),
    onSettled: settleWatch,
  });
  const retireWatch = useMutation<WriteResult, Error, string>({
    mutationFn: (id) =>
      source === null ? none() : source.retireWatchTicker(id),
    onSettled: settleWatch,
  });

  const manualRows: readonly ManualPosition[] = manual.data?.rows ?? [];
  const watchRows: readonly WatchTicker[] = watch.data?.rows ?? [];
  return {
    manual: manualRows,
    watch: watchRows,
    loading: on && (manual.isPending || watch.isPending),
    /** A failed read of either list, as a sentence. */
    readError: manual.data?.transportError ?? watch.data?.transportError ?? null,
    saveManual,
    retireManual,
    saveWatch,
    retireWatch,
  };
}
