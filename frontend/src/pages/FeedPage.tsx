import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownWideNarrow, ArrowLeft, ArrowRight, Check, ExternalLink, Heart, MessageCircle, MoreVertical, RefreshCw, RotateCcw, Search, Sparkles, Trash2 } from 'lucide-react';
import { api, toQuery, userMessage } from '../api';
import { Badge, Button, EmptyState, PageHeading, Select, Spinner, type Notice } from '../components';
import type { CollectResult, Keyword, Post, PostPage } from '../types';

type Props = { notify: (notice: Notice) => void; onGoToKeywords: () => void };

function formatDate(value: string | null): string {
  if (!value) return 'Дата неизвестна';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Дата неизвестна';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}

function PostActionsMenu({ post, busy, onDelete }: { post: Post; busy: boolean; onDelete: (post: Post) => void }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: PointerEvent) {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) setOpen(false);
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  return (
    <div className="relative shrink-0" ref={menuRef}>
      <button
        type="button"
        aria-label={`Действия с постом @${post.author_username || 'unknown'}`}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={busy}
        onClick={() => setOpen((current) => !current)}
        className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-[#758187] transition hover:bg-[#eef1f2] hover:text-ink disabled:opacity-50"
      >
        <MoreVertical className="h-4 w-4" aria-hidden="true" />
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-20 min-w-44 rounded-xl border border-[#e5e9ea] bg-white p-1.5 shadow-lg" role="menu">
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={() => { setOpen(false); onDelete(post); }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium text-[#a44739] transition hover:bg-[#fff3f0] disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            Удалить пост
          </button>
        </div>
      )}
    </div>
  );
}

function PostCard({
  post,
  selected,
  onSelect,
  onStatus,
  onDelete,
  busy = false,
}: {
  post: Post;
  selected: boolean;
  onSelect: (post: Post, selected: boolean) => void;
  onStatus: (post: Post, patch: { is_read?: boolean; is_relevant?: boolean }) => void;
  onDelete: (post: Post) => void;
  busy?: boolean;
}) {
  const isSafeLink = Boolean(post.permalink && /^https?:\/\//i.test(post.permalink));

  return (
    <article className={`rounded-2xl border bg-white p-5 shadow-card transition sm:p-6 ${post.is_read ? 'border-[#e5e9ea]' : 'border-[#dce7cf] ring-1 ring-[#edf4e4]'}`}>
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          aria-label={`Выбрать пост @${post.author_username || 'unknown'}`}
          checked={selected}
          disabled={busy}
          onChange={(event) => onSelect(post, event.target.checked)}
          className="mt-3 h-4 w-4 shrink-0 cursor-pointer accent-[#79a93c] disabled:cursor-not-allowed"
        />
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#eef2ed] text-sm font-bold text-[#51635a]">
          {(post.author_username || '?').slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-semibold text-ink">@{post.author_username || 'unknown'}</span>
              {!post.is_read && <span className="h-1.5 w-1.5 rounded-full bg-[#89b94e]" aria-label="Новый пост" />}
              <span className="text-xs text-[#89959a]">{formatDate(post.post_timestamp)}</span>
            </div>
            <PostActionsMenu post={post} busy={busy} onDelete={onDelete} />
          </div>
          <div className="mt-3 whitespace-pre-wrap break-words text-[14px] leading-6 text-[#354248]">{post.text || 'Пост без текста'}</div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {post.matched_keyword && <Badge tone="blue"><span className="mr-1 opacity-70">Ключ:</span>{post.matched_keyword}</Badge>}
            {!post.is_relevant && <Badge tone="amber">Нерелевантный</Badge>}
            {post.is_duplicate && <Badge tone="neutral">Дубликат{post.duplicate_of_id ? ` · #${post.duplicate_of_id}` : ''}</Badge>}
          </div>
          <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-[#f0f2f3] pt-4">
            <div className="flex items-center gap-4 text-xs text-[#859196]">
              <span className="inline-flex items-center gap-1.5"><Heart className="h-3.5 w-3.5" />{post.likes_count}</span>
              <span className="inline-flex items-center gap-1.5"><MessageCircle className="h-3.5 w-3.5" />{post.replies_count}</span>
              {isSafeLink ? (
                <a href={post.permalink} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 font-semibold text-[#607a47] hover:text-ink">
                  Открыть в Threads <ExternalLink className="h-3 w-3" />
                </a>
              ) : (
                <span className="text-xs text-[#9aa4a8]">Ссылка недоступна</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button variant="quiet" disabled={busy} className="min-h-9 rounded-lg px-2.5 text-xs" onClick={() => onStatus(post, { is_read: !post.is_read })}>
                <Check className="h-3.5 w-3.5" />{post.is_read ? 'Вернуть в новые' : 'Прочитано'}
              </Button>
              {post.is_relevant && (
                <Button variant="quiet" disabled={busy} className="min-h-9 rounded-lg px-2.5 text-xs text-[#9a665b] hover:bg-[#fff3f0] hover:text-[#9a4235]" onClick={() => onStatus(post, { is_relevant: false })}>
                  Нерелевантно
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </article>
  );
}

export default function FeedPage({ notify, onGoToKeywords }: Props) {
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [keywordId, setKeywordId] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [hideIrrelevant, setHideIrrelevant] = useState(true);
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PostPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [collecting, setCollecting] = useState(false);
  const [busyPostIds, setBusyPostIds] = useState<number[]>([]);
  const [selectedPostIds, setSelectedPostIds] = useState<number[]>([]);
  const [bulkAction, setBulkAction] = useState<'status' | 'delete' | null>(null);
  const [feedRefreshKey, setFeedRefreshKey] = useState(0);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const loadKeywords = useCallback(async () => {
    try {
      const items = await api<Keyword[]>('/keywords');
      setKeywords(items);
    } catch {
      // Kept intact if previously loaded
    }
  }, []);

  useEffect(() => {
    void loadKeywords();
  }, [loadKeywords]);

  const query = useMemo(() => toQuery({
    page,
    page_size: 20,
    keyword_id: keywordId ? Number(keywordId) : undefined,
    unread_only: unreadOnly,
    hide_irrelevant: hideIrrelevant,
    include_duplicates: includeDuplicates,
    q: debouncedSearch.trim() || undefined,
  }), [page, keywordId, unreadOnly, hideIrrelevant, includeDuplicates, debouncedSearch]);

  useEffect(() => {
    setSelectedPostIds([]);
  }, [query]);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    setLoadError('');

    void api<PostPage>(`/posts${query}`, { signal: controller.signal })
      .then((posts) => {
        if (!alive) return;
        setData(posts);
      })
      .catch((error: unknown) => {
        if (!alive || (error instanceof DOMException && error.name === 'AbortError')) return;
        setLoadError(userMessage(error));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
      controller.abort();
    };
  }, [query, feedRefreshKey]);

  async function updatePost(post: Post, patch: { is_read?: boolean; is_relevant?: boolean }) {
    setBusyPostIds((current) => [...current, post.id]);
    try {
      await api<Post>(`/posts/${post.id}/status`, { method: 'PATCH', body: JSON.stringify(patch) });
      setSelectedPostIds((current) => current.filter((id) => id !== post.id));
      setData((current) => current ? { ...current, items: current.items.map((item) => item.id === post.id ? { ...item, ...patch } : item) } : current);
      if ((hideIrrelevant && patch.is_relevant === false) || (unreadOnly && patch.is_read === true)) {
        setData((current) => current ? { ...current, items: current.items.filter((item) => item.id !== post.id), total: Math.max(0, current.total - 1) } : current);
      }
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setBusyPostIds((current) => current.filter((id) => id !== post.id));
    }
  }

  async function deletePost(post: Post) {
    if (!window.confirm(`Удалить пост @${post.author_username || 'unknown'}? Это действие нельзя отменить.`)) return;
    setBusyPostIds((current) => [...current, post.id]);
    try {
      await api<void>(`/posts/${post.id}`, { method: 'DELETE' });
      setSelectedPostIds((current) => current.filter((id) => id !== post.id));
      if (data?.items.length === 1 && page > 1) {
        setPage((current) => Math.max(1, current - 1));
      } else {
        setData((current) => current ? {
          ...current,
          items: current.items.filter((item) => item.id !== post.id),
          total: Math.max(0, current.total - 1),
        } : current);
      }
      notify({ kind: 'success', message: 'Пост удалён.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setBusyPostIds((current) => current.filter((id) => id !== post.id));
    }
  }

  async function updateSelectedPosts(patch: { is_read?: boolean; is_relevant?: boolean }, actionLabel: string) {
    const ids = [...selectedPostIds];
    if (!ids.length || busyPostIds.length > 0) return;

    setBulkAction('status');
    const results = await Promise.allSettled(ids.map((id) => api<Post>(`/posts/${id}/status`, { method: 'PATCH', body: JSON.stringify(patch) })));
    const succeededIds = ids.filter((_, index) => results[index].status === 'fulfilled');
    const succeededSet = new Set(succeededIds);
    const visibleItemCount = data?.items.length ?? 0;
    const removedOnPage = data?.items.filter((item) => succeededSet.has(item.id) && ((hideIrrelevant && patch.is_relevant === false) || (unreadOnly && patch.is_read === true))).length ?? 0;
    const allVisibleItemsRemoved = visibleItemCount > 0 && removedOnPage === visibleItemCount;

    setData((current) => {
      if (!current) return current;
      const updated = current.items.map((item) => succeededSet.has(item.id) ? { ...item, ...patch } : item);
      const visible = updated.filter((item) => !((hideIrrelevant && patch.is_relevant === false && succeededSet.has(item.id)) || (unreadOnly && patch.is_read === true && succeededSet.has(item.id))));
      return { ...current, items: visible, total: Math.max(0, current.total - (updated.length - visible.length)) };
    });
    setSelectedPostIds([]);
    setBulkAction(null);

    const failedCount = ids.length - succeededIds.length;
    if (failedCount === 0) {
      notify({ kind: 'success', message: `${actionLabel}: ${succeededIds.length}.` });
    } else {
      const failure = results.find((result) => result.status === 'rejected');
      notify({ kind: 'error', message: `Успешно ${succeededIds.length} из ${ids.length}. ${failure?.status === 'rejected' ? userMessage(failure.reason) : 'Не удалось обновить часть постов.'}` });
    }
    if (allVisibleItemsRemoved && page > 1) setPage((current) => Math.max(1, current - 1));
    else if (allVisibleItemsRemoved && page === 1 && (data?.total ?? 0) > removedOnPage) setFeedRefreshKey((current) => current + 1);
  }

  async function deleteSelectedPosts() {
    const ids = [...selectedPostIds];
    if (!ids.length || busyPostIds.length > 0) return;
    if (!window.confirm(`Удалить выбранные посты (${ids.length})? Это действие нельзя отменить.`)) return;

    setBulkAction('delete');
    const results = await Promise.allSettled(ids.map((id) => api<void>(`/posts/${id}`, { method: 'DELETE' })));
    const succeededIds = ids.filter((_, index) => results[index].status === 'fulfilled');
    const succeededSet = new Set(succeededIds);
    const visibleItemCount = data?.items.length ?? 0;
    const allVisibleItemsDeleted = visibleItemCount > 0 && succeededIds.length === visibleItemCount;
    setData((current) => current ? {
      ...current,
      items: current.items.filter((item) => !succeededSet.has(item.id)),
      total: Math.max(0, current.total - succeededIds.length),
    } : current);
    setSelectedPostIds([]);
    setBulkAction(null);

    const failedCount = ids.length - succeededIds.length;
    if (failedCount === 0) {
      notify({ kind: 'success', message: `Удалено постов: ${succeededIds.length}.` });
    } else {
      const failure = results.find((result) => result.status === 'rejected');
      notify({ kind: 'error', message: `Удалено ${succeededIds.length} из ${ids.length}. ${failure?.status === 'rejected' ? userMessage(failure.reason) : 'Не удалось удалить часть постов.'}` });
    }

    if (allVisibleItemsDeleted && page > 1) setPage((current) => Math.max(1, current - 1));
    else if (allVisibleItemsDeleted && page === 1 && (data?.total ?? 0) > succeededIds.length) setFeedRefreshKey((current) => current + 1);
  }

  async function collectNow() {
    setCollecting(true);
    try {
      const results = await api<CollectResult[]>('/keywords/collect', { method: 'POST' });
      const saved = results.reduce((sum, result) => sum + result.saved, 0);
      const scanned = results.reduce((sum, result) => sum + result.scanned, 0);
      const failures = results.filter((result) => result.error).length;
      const message = failures
        ? `Сбор завершён с ошибками: сохранено ${saved}, проверено ${scanned}; не удалось обработать фраз: ${failures}.`
        : `Сбор завершён: проверено ${scanned}, сохранено новых постов: ${saved}.`;
      notify({ kind: failures ? 'error' : 'success', message });
      void loadKeywords();
      setPage(1);
      const fresh = await api<PostPage>(`/posts${toQuery({ page: 1, page_size: 20, keyword_id: keywordId ? Number(keywordId) : undefined, unread_only: unreadOnly, hide_irrelevant: hideIrrelevant, include_duplicates: includeDuplicates, q: debouncedSearch.trim() || undefined })}`);
      setData(fresh);
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setCollecting(false);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const activeKeywords = keywords.filter((keyword) => keyword.is_active).length;
  const feedBusy = bulkAction !== null || busyPostIds.length > 0;

  return (
    <div className="animate-enter">
      <PageHeading
        eyebrow="Мониторинг Threads"
        title="Лента упоминаний"
        description="Новые публикации по вашим ключевым словам — в одном месте."
        action={<Button onClick={() => void collectNow()} disabled={collecting || activeKeywords === 0} className="min-h-11 bg-[#c5f36a] text-ink hover:bg-[#b5e45c] disabled:bg-[#e3e8df]">
          <RefreshCw className={`h-4 w-4 ${collecting ? 'animate-spin' : ''}`} />{collecting ? 'Собираем…' : 'Запустить сбор'}
        </Button>}
      />

      <section className="mb-5 grid gap-3 sm:grid-cols-3" aria-label="Сводка ленты">
        <div className="rounded-2xl border border-[#e5e9ea] bg-white px-5 py-4 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[.12em] text-[#849096]">Всего публикаций</div>
          <div className="mt-2 text-2xl font-bold tracking-[-.04em]">{data?.total ?? '—'}</div>
        </div>
        <div className="rounded-2xl border border-[#e5e9ea] bg-white px-5 py-4 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[.12em] text-[#849096]">Активные фразы</div>
          <div className="mt-2 text-2xl font-bold tracking-[-.04em]">{activeKeywords}</div>
        </div>
        <div className="rounded-2xl border border-[#e5e9ea] bg-white px-5 py-4 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[.12em] text-[#849096]">На этой странице</div>
          <div className="mt-2 text-2xl font-bold tracking-[-.04em]">{data?.items.length ?? '—'}<span className="ml-1 text-sm font-medium text-[#849096]">постов</span></div>
        </div>
      </section>

      <section className="mb-5 rounded-2xl border border-[#e5e9ea] bg-white p-4 shadow-card sm:p-5" aria-label="Фильтры ленты">
        <div className="grid gap-4 xl:grid-cols-[minmax(220px,1fr)_minmax(190px,.7fr)_auto] xl:items-end">
          <label className="grid gap-2 text-sm font-medium text-[#344248]">
            <span>Поиск в постах</span>
            <span className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#94a0a4]" />
              <input className="h-11 w-full rounded-xl border border-[#dfe4e6] bg-white pl-9 pr-3.5 text-sm placeholder:text-[#a0aaae] focus:border-[#9abd61] focus:outline-none" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Автор или текст…" />
            </span>
          </label>
          <Select label="Ключевая фраза" value={keywordId} onChange={(event) => { setKeywordId(event.target.value); setPage(1); }}>
            <option value="">Все фразы</option>
            {keywords.map((keyword) => <option key={keyword.id} value={keyword.id}>{keyword.phrase}{keyword.is_active ? '' : ' · выключена'}</option>)}
          </Select>
          <div className="flex flex-wrap gap-x-5 gap-y-3 pb-1 xl:justify-end">
            <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-[#55646a]"><input type="checkbox" className="h-4 w-4 accent-[#79a93c]" checked={unreadOnly} onChange={(event) => { setUnreadOnly(event.target.checked); setPage(1); }} />Только новые</label>
            <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-[#55646a]"><input type="checkbox" className="h-4 w-4 accent-[#79a93c]" checked={hideIrrelevant} onChange={(event) => { setHideIrrelevant(event.target.checked); setPage(1); }} />Скрыть нерелевантные</label>
            <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-[#55646a]"><input type="checkbox" className="h-4 w-4 accent-[#79a93c]" checked={includeDuplicates} onChange={(event) => { setIncludeDuplicates(event.target.checked); setPage(1); }} />Показать дубликаты</label>
          </div>
        </div>
      </section>

      {loading && <div className="flex min-h-56 items-center justify-center rounded-2xl border border-[#e5e9ea] bg-white"><Spinner label="Загружаем ленту…" /></div>}
      {!loading && loadError && <div className="rounded-2xl border border-[#f0d4cf] bg-white p-6 text-sm text-[#98473b]">Не удалось загрузить публикации: {loadError}</div>}
      {!loading && !loadError && data?.items.length === 0 && (
        <EmptyState
          icon={activeKeywords ? ArrowDownWideNarrow : Sparkles}
          title={activeKeywords ? 'По этим условиям ничего не найдено' : 'Добавьте первую ключевую фразу'}
          description={activeKeywords ? 'Попробуйте изменить фильтры или запустите сбор, чтобы проверить свежие публикации.' : 'Добавьте название бренда или другую поисковую фразу, а затем запустите первый сбор.'}
          action={activeKeywords ? <Button variant="secondary" onClick={() => void collectNow()} disabled={collecting}><RefreshCw className={`h-4 w-4 ${collecting ? 'animate-spin' : ''}`} />Запустить сбор</Button> : <Button onClick={onGoToKeywords}>Настроить фразы</Button>}
        />
      )}
      {!loading && !loadError && Boolean(data?.items.length) && (
        <>
          <div className="mb-3 flex items-center justify-between text-xs text-[#89959a]">
            <span>Показаны публикации {((page - 1) * 20) + 1}–{Math.min(page * 20, data!.total)} из {data!.total}</span>
            <span>Сначала новые</span>
          </div>
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-2xl border border-[#e5e9ea] bg-white p-3 shadow-card sm:p-4">
            <label className="mr-auto inline-flex cursor-pointer items-center gap-2 px-1 text-sm font-medium text-[#55646a]">
              <input
                type="checkbox"
                checked={Boolean(data!.items.length) && data!.items.every((post) => selectedPostIds.includes(post.id))}
                disabled={feedBusy}
                onChange={(event) => setSelectedPostIds(event.target.checked ? data!.items.map((post) => post.id) : [])}
                className="h-4 w-4 cursor-pointer accent-[#79a93c] disabled:cursor-not-allowed"
              />
              Выбрать все на странице
            </label>
            {selectedPostIds.length > 0 && (
              <>
                <span className="px-1 text-xs font-semibold text-[#758187]">Выбрано: {selectedPostIds.length}</span>
                <Button variant="secondary" className="min-h-9 rounded-lg px-3 text-xs" disabled={feedBusy} onClick={() => void updateSelectedPosts({ is_read: true }, 'Прочитано постов')}>
                  <Check className="h-3.5 w-3.5" />Прочитано
                </Button>
                <Button variant="secondary" className="min-h-9 rounded-lg px-3 text-xs" disabled={feedBusy} onClick={() => void updateSelectedPosts({ is_read: false }, 'Возвращено в статус новых')}>
                  <RotateCcw className="h-3.5 w-3.5" />В новые
                </Button>
                <Button variant="quiet" className="min-h-9 rounded-lg px-3 text-xs text-[#9a665b] hover:bg-[#fff3f0] hover:text-[#9a4235]" disabled={feedBusy} onClick={() => void updateSelectedPosts({ is_relevant: false }, 'Постов помечено нерелевантными')}>
                  Нерелевантно
                </Button>
                <Button variant="danger" className="min-h-9 rounded-lg px-3 text-xs" disabled={feedBusy} onClick={() => void deleteSelectedPosts()}>
                  <Trash2 className="h-3.5 w-3.5" />Удалить
                </Button>
              </>
            )}
            {bulkAction && <span className="px-1 text-xs text-[#758187]" role="status">{bulkAction === 'delete' ? 'Удаляем…' : 'Сохраняем…'}</span>}
          </div>
          <div className="grid gap-3">
            {data!.items.map((post) => (
              <PostCard
                key={post.id}
                post={post}
                selected={selectedPostIds.includes(post.id)}
                onSelect={(item, selected) => setSelectedPostIds((current) => selected ? [...new Set([...current, item.id])] : current.filter((id) => id !== item.id))}
                busy={busyPostIds.includes(post.id) || bulkAction !== null}
                onStatus={(item, patch) => void updatePost(item, patch)}
                onDelete={(item) => void deletePost(item)}
              />
            ))}
          </div>
          <nav className="mt-5 flex items-center justify-between rounded-2xl border border-[#e5e9ea] bg-white px-4 py-3 shadow-card" aria-label="Страницы ленты">
            <Button variant="secondary" className="min-h-9 px-3" disabled={page <= 1 || loading} onClick={() => setPage((current) => Math.max(1, current - 1))}><ArrowLeft className="h-4 w-4" /><span className="hidden sm:inline">Назад</span></Button>
            <span className="text-sm text-[#68777d]">Страница <strong className="text-ink">{page}</strong> из {totalPages}</span>
            <Button variant="secondary" className="min-h-9 px-3" disabled={page >= totalPages || loading} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}><span className="hidden sm:inline">Дальше</span><ArrowRight className="h-4 w-4" /></Button>
          </nav>
        </>
      )}
      {busyPostIds.length > 0 && <div className="sr-only" role="status">Сохраняем изменения публикации</div>}
    </div>
  );
}
