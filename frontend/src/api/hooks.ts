import {
  QueryClient,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type MutateOptions,
  type UseMutationOptions,
} from "@tanstack/react-query";
import { api, ApiError, NetworkError, newIdempotencyKey, type Mutation } from "./client";
import type { Item, ItemFilters, Me } from "./types";

export const keys = {
  me: ["me"] as const,
  dashboard: ["dashboard"] as const,
  items: (filters: ItemFilters) => ["items", filters] as const,
  allItems: ["items"] as const,
  item: (id: number) => ["item", id] as const,
  activity: (id: number) => ["activity", id] as const,
  similar: (id: number) => ["similar", id] as const,
  members: (teamId: number) => ["members", teamId] as const,
  notifications: ["notifications"] as const,
};

// No websockets: lists poll, and everything refetches when the tab regains focus.
const POLL_MS = 20_000;

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        refetchOnWindowFocus: true,
        // Retrying a 4xx won't change the answer; network blips are worth a couple of tries.
        retry: (count, err) => !(err instanceof ApiError) && count < 2,
      },
      mutations: {
        // Safe only because every mutation sends an Idempotency-Key and retries reuse it: TanStack retries
        // with the same variables, and the key lives in the variables.
        retry: (count, err) => err instanceof NetworkError && count < 2,
        retryDelay: (attempt) => 500 * 2 ** attempt,
      },
    },
  });
}

// ---- reads ---------------------------------------------------------------------------------------------

export const useMe = (enabled = true) => useQuery({ queryKey: keys.me, queryFn: api.me, enabled, staleTime: 60_000 });

export const useDashboard = () =>
  useQuery({ queryKey: keys.dashboard, queryFn: api.dashboard, refetchInterval: POLL_MS });

export const useItems = (filters: ItemFilters, limit = 25) =>
  useInfiniteQuery({
    queryKey: [...keys.items(filters), limit],
    queryFn: ({ pageParam }) => api.items(filters, pageParam, limit),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: POLL_MS,
  });

export const useItem = (id: number) =>
  useQuery({ queryKey: keys.item(id), queryFn: () => api.item(id), refetchInterval: POLL_MS });

export const useActivity = (id: number) =>
  useInfiniteQuery({
    queryKey: keys.activity(id),
    queryFn: ({ pageParam }) => api.activity(id, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: POLL_MS,
  });

// Similar items change only when someone edits text, so this doesn't poll.
export const useSimilar = (id: number) =>
  useQuery({ queryKey: keys.similar(id), queryFn: () => api.similar(id), staleTime: 60_000 });

export const useMembers = (teamId: number, enabled = true) =>
  useQuery({ queryKey: keys.members(teamId), queryFn: () => api.members(teamId), enabled, staleTime: 60_000 });

export const useNotifications = () =>
  useQuery({ queryKey: keys.notifications, queryFn: api.notifications, refetchInterval: POLL_MS });

// ---- writes --------------------------------------------------------------------------------------------

/**
 * A mutation whose variables carry an Idempotency-Key. `run(vars)` is one user action and gets a fresh key;
 * automatic retries and `retry()` (the "Try again" button) resend the same variables, so the same key,
 * and the server replays the first result instead of doing the work twice.
 */
export function useKeyedMutation<TVars, TData>(
  mutationFn: (vars: TVars & Mutation) => Promise<TData>,
  options: Omit<UseMutationOptions<TData, Error, TVars & Mutation, unknown>, "mutationFn"> = {},
) {
  const m = useMutation<TData, Error, TVars & Mutation, unknown>({ mutationFn, ...options });
  return {
    ...m,
    run: (vars: TVars, callbacks?: MutateOptions<TData, Error, TVars & Mutation, unknown>) =>
      m.mutate({ ...vars, key: newIdempotencyKey() } as TVars & Mutation, callbacks),
    retry: () => m.variables && m.mutate(m.variables),
  };
}

/** After any change to an item: cache the server's copy and refresh what depends on it. */
function useItemSync() {
  const qc = useQueryClient();
  return {
    saved(item: Item) {
      qc.setQueryData(keys.item(item.id), item);
      qc.invalidateQueries({ queryKey: keys.activity(item.id) });
      qc.invalidateQueries({ queryKey: keys.allItems });
      qc.invalidateQueries({ queryKey: keys.dashboard });
    },
    /** 409s carry the item as it is now; show that rather than our stale copy. */
    failed(id: number, err: Error) {
      if (err instanceof ApiError && err.currentItem) {
        qc.setQueryData(keys.item(id), err.currentItem);
        qc.invalidateQueries({ queryKey: keys.activity(id) });
      } else if (err instanceof ApiError && err.status === 409) {
        qc.invalidateQueries({ queryKey: keys.item(id) });
      }
    },
  };
}

export function useUpdateItem(id: number) {
  const sync = useItemSync();
  return useKeyedMutation(api.updateItem, { onSuccess: sync.saved, onError: (e) => sync.failed(id, e) });
}

export function useAssign(id: number) {
  const sync = useItemSync();
  return useKeyedMutation(api.assign, { onSuccess: sync.saved, onError: (e) => sync.failed(id, e) });
}

export function useTransition(id: number) {
  const sync = useItemSync();
  return useKeyedMutation(api.transition, { onSuccess: sync.saved, onError: (e) => sync.failed(id, e) });
}

/** Claim is optimistic: the item shows as yours immediately and rolls back if someone else got it first. */
export function useClaim(id: number, me: Me | undefined) {
  const qc = useQueryClient();
  const sync = useItemSync();
  return useKeyedMutation<{ id: number }, Item>(api.claim, {
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: keys.item(id) });
      const previous = qc.getQueryData<Item>(keys.item(id));
      if (previous && me) {
        qc.setQueryData<Item>(keys.item(id), {
          ...previous,
          status: "IN_PROGRESS",
          assignee: { id: me.id, name: me.name },
          allowed_actions: [],
        });
      }
      return { previous };
    },
    onSuccess: sync.saved,
    onError: (err, _vars, context) => {
      const previous = (context as { previous?: Item } | undefined)?.previous;
      if (previous) qc.setQueryData(keys.item(id), previous);
      sync.failed(id, err);
    },
  });
}

export function useAddComment(id: number) {
  const qc = useQueryClient();
  return useKeyedMutation(api.comment, {
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.activity(id) }),
  });
}

export function useCreateItem() {
  const qc = useQueryClient();
  return useKeyedMutation(api.createItem, {
    onSuccess: (item) => {
      qc.setQueryData(keys.item(item.id), item);
      qc.invalidateQueries({ queryKey: keys.allItems });
      qc.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.markRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.notifications }),
  });
}

export function useMarkAllRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.markAllRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.notifications }),
  });
}
