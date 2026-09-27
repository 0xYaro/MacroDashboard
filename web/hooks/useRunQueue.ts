"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { keys, type WriteResult } from "@/lib/data";
import { useSource } from "./useSource";

/**
 * The Desk's two writes: ask for a refresh, and decide a proposal.
 *
 * Each settles by re-reading the queue, never by writing the answer into the
 * cache itself: the row the operator sees next is the row the database holds,
 * including a refusal the function raised.
 */
export function useRunQueue() {
  const { source } = useSource();
  const client = useQueryClient();
  const scope = source?.scope ?? "none";
  const settle = () =>
    client.invalidateQueries({ queryKey: keys.runRequests(scope) });

  const refresh = useMutation<WriteResult, Error, string>({
    mutationFn: async (reason) =>
      source === null
        ? { ok: false, reason: "No data source is configured." }
        : source.requestRefresh(reason),
    onSettled: settle,
  });
  const decide = useMutation<
    WriteResult,
    Error,
    { id: number; decision: "accept" | "reject" }
  >({
    mutationFn: async ({ id, decision }) =>
      source === null
        ? { ok: false, reason: "No data source is configured." }
        : source.decideProposal(id, decision),
    onSettled: settle,
  });
  return { refresh, decide };
}
