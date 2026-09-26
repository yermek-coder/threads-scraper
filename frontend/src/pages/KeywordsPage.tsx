import { useCallback, useEffect, useState } from 'react';
import { Check, CirclePlus, Pencil, Plus, Search, Tag, Trash2, X } from 'lucide-react';
import { api, userMessage } from '../api';
import { Badge, Button, IconButton, Input, PageHeading, Spinner, type Notice } from '../components';
import type { Keyword, StopWord } from '../types';

type Props = { notify: (notice: Notice) => void };

export default function KeywordsPage({ notify }: Props) {
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [stopwords, setStopwords] = useState<StopWord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [phrase, setPhrase] = useState('');
  const [stopwordInput, setStopwordInput] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editPhrase, setEditPhrase] = useState('');

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [keywordRows, stopwordRows] = await Promise.all([api<Keyword[]>('/keywords'), api<StopWord[]>('/stopwords')]);
      setKeywords(keywordRows);
      setStopwords(stopwordRows);
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => { void reload(); }, [reload]);

  async function addKeyword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!phrase.trim()) return;
    setSaving(true);
    try {
      const created = await api<Keyword>('/keywords', { method: 'POST', body: JSON.stringify({ phrase: phrase.trim(), is_active: true }) });
      setKeywords((current) => [...current, created]);
      setPhrase('');
      notify({ kind: 'success', message: 'Ключевая фраза добавлена.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  async function toggleKeyword(keyword: Keyword) {
    try {
      const updated = await api<Keyword>(`/keywords/${keyword.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !keyword.is_active }) });
      setKeywords((current) => current.map((item) => item.id === keyword.id ? updated : item));
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    }
  }

  async function removeKeyword(keyword: Keyword) {
    if (!window.confirm(`Удалить поисковую фразу «${keyword.phrase}»? Уже собранные публикации останутся в ленте.`)) return;
    try {
      await api<void>(`/keywords/${keyword.id}`, { method: 'DELETE' });
      setKeywords((current) => current.filter((item) => item.id !== keyword.id));
      notify({ kind: 'success', message: 'Фраза удалена. Найденные ранее посты сохранены.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    }
  }

  async function saveEdit(keyword: Keyword) {
    if (!editPhrase.trim()) return;
    try {
      const updated = await api<Keyword>(`/keywords/${keyword.id}`, { method: 'PATCH', body: JSON.stringify({ phrase: editPhrase.trim() }) });
      setKeywords((current) => current.map((item) => item.id === keyword.id ? updated : item));
      setEditingId(null);
      notify({ kind: 'success', message: 'Поисковая фраза обновлена.' });
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    }
  }

  async function addStopwords(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const rawTerms = [...new Set(stopwordInput.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean))];
    if (!rawTerms.length) return;
    const existing = new Set(stopwords.map((item) => item.word_or_phrase.toLowerCase()));
    const terms = rawTerms.filter((term) => !existing.has(term.toLowerCase()));
    if (!terms.length) {
      notify({ kind: 'error', message: 'Все указанные слова или фразы уже есть в списке.' });
      return;
    }
    setSaving(true);
    const outcomes = await Promise.allSettled(terms.map((word_or_phrase) => api<StopWord>('/stopwords', { method: 'POST', body: JSON.stringify({ word_or_phrase }) })));
    const created = outcomes.flatMap((outcome) => outcome.status === 'fulfilled' ? [outcome.value] : []);
    setStopwords((current) => [...current, ...created]);
    setStopwordInput('');
    const duplicatesSkipped = rawTerms.length - terms.length;
    const failed = outcomes.length - created.length;
    if (failed > 0 || duplicatesSkipped > 0) {
      notify({ kind: created.length ? 'success' : 'error', message: `Добавлено: ${created.length}${duplicatesSkipped ? `, пропущено дубликатов: ${duplicatesSkipped}` : ''}${failed ? `, ошибок: ${failed}` : ''}.` });
    } else {
      notify({ kind: 'success', message: `Добавлено стоп-слов: ${created.length}.` });
    }
    setSaving(false);
  }

  async function removeStopword(word: StopWord) {
    if (!window.confirm(`Удалить стоп-слово «${word.word_or_phrase}»?`)) return;
    try {
      await api<void>(`/stopwords/${word.id}`, { method: 'DELETE' });
      setStopwords((current) => current.filter((item) => item.id !== word.id));
    } catch (error) {
      notify({ kind: 'error', message: userMessage(error) });
    }
  }

  return (
    <div className="animate-enter">
      <PageHeading title="Ключевые слова" description="Добавляйте поисковые фразы и исключайте публикации со стоп-словами." />

      <section className="mb-5 rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#edf5e3] text-[#66873a]"><Search className="h-5 w-5" /></div>
          <div><h2 className="font-bold text-ink">Поисковые фразы</h2><p className="mt-1 text-sm text-muted">Каждая активная фраза проверяется по расписанию и вручную.</p></div>
          <Badge tone="green" >{keywords.filter((item) => item.is_active).length} активно</Badge>
        </div>
        <form className="mb-5 flex flex-col gap-2 sm:flex-row" onSubmit={(event) => void addKeyword(event)}>
          <Input aria-label="Новая ключевая фраза" value={phrase} onChange={(event) => setPhrase(event.target.value)} placeholder="Например, название бренда" maxLength={255} />
          <Button type="submit" disabled={saving || !phrase.trim()} className="shrink-0"><Plus className="h-4 w-4" />Добавить фразу</Button>
        </form>
        {loading ? <div className="flex min-h-36 items-center justify-center"><Spinner label="Загружаем фразы…" /></div> : keywords.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[#dce2e4] px-5 py-9 text-center text-sm text-muted">Список пока пуст. Добавьте первую поисковую фразу выше.</div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-[#e7ebec]">
            <div className="hidden grid-cols-[minmax(0,1fr)_130px_150px_92px] gap-3 bg-[#f8f9f9] px-4 py-3 text-[11px] font-bold uppercase tracking-[.12em] text-[#879398] sm:grid"><span>Поисковая фраза</span><span>Состояние</span><span>Последний сбор</span><span className="text-right">Действия</span></div>
            <ul className="divide-y divide-[#edf0f1]">
              {keywords.map((keyword) => (
                <li key={keyword.id} className="grid gap-3 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_130px_150px_92px] sm:items-center">
                  <div className="min-w-0">
                    {editingId === keyword.id ? (
                      <div className="flex gap-2">
                        <input autoFocus value={editPhrase} onChange={(event) => setEditPhrase(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void saveEdit(keyword); if (event.key === 'Escape') setEditingId(null); }} className="h-9 min-w-0 flex-1 rounded-lg border border-[#dfe4e6] px-3 text-sm focus:border-[#9abd61] focus:outline-none" maxLength={255} aria-label="Изменить поисковую фразу" />
                        <IconButton label="Сохранить" icon={Check} onClick={() => void saveEdit(keyword)} className="text-[#5f8431]" />
                        <IconButton label="Отменить" icon={X} onClick={() => setEditingId(null)} />
                      </div>
                    ) : <span className="break-words text-sm font-semibold text-ink">{keyword.phrase}</span>}
                    <div className="mt-1 text-xs text-[#98a2a6] sm:hidden">Последний сбор: {keyword.last_polled_at ? new Date(keyword.last_polled_at).toLocaleString('ru-RU') : 'ещё не запускался'}</div>
                  </div>
                  <div className="flex items-center justify-between gap-3 sm:justify-start">
                    <span className="text-xs text-[#879398] sm:hidden">Состояние</span>
                    <button type="button" role="switch" aria-checked={keyword.is_active} aria-label={`${keyword.is_active ? 'Выключить' : 'Включить'} фразу ${keyword.phrase}`} onClick={() => void toggleKeyword(keyword)} className={`relative h-7 w-12 rounded-full transition ${keyword.is_active ? 'bg-[#8bb64e]' : 'bg-[#cbd2d5]'}`}>
                      <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow-sm transition-all ${keyword.is_active ? 'left-6' : 'left-1'}`} />
                    </button>
                  </div>
                  <span className="hidden text-xs text-[#758187] sm:block">{keyword.last_polled_at ? new Date(keyword.last_polled_at).toLocaleString('ru-RU') : 'Ещё не запускался'}</span>
                  <div className="flex justify-end gap-1">
                    {editingId !== keyword.id && <IconButton label={`Изменить ${keyword.phrase}`} icon={Pencil} onClick={() => { setEditingId(keyword.id); setEditPhrase(keyword.phrase); }} />}
                    <IconButton label={`Удалить ${keyword.phrase}`} icon={Trash2} onClick={() => void removeKeyword(keyword)} className="hover:bg-[#fff0ec] hover:text-[#a44739]" />
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-[#e5e9ea] bg-white p-5 shadow-card sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#f2eef9] text-[#8269a9]"><Tag className="h-5 w-5" /></div>
          <div><h2 className="font-bold text-ink">Стоп-слова</h2><p className="mt-1 text-sm text-muted">Публикации с этими словами или фразами будут отмечены как нерелевантные.</p></div>
          <Badge>{stopwords.length} слов</Badge>
        </div>
        <form className="grid gap-3 md:grid-cols-[1fr_auto] md:items-end" onSubmit={(event) => void addStopwords(event)}>
          <label className="grid gap-2 text-sm font-medium text-[#344248]"><span>Добавить слова или фразы</span><textarea className="min-h-[76px] rounded-xl border border-[#dfe4e6] px-3.5 py-3 text-sm placeholder:text-[#a0aaae] focus:border-[#9abd61] focus:outline-none" value={stopwordInput} onChange={(event) => setStopwordInput(event.target.value)} placeholder="По одному слову на строку или разделите запятыми" /></label>
          <Button type="submit" disabled={saving || !stopwordInput.trim()} className="md:mb-0.5"><CirclePlus className="h-4 w-4" />Добавить</Button>
        </form>
        {stopwords.length === 0 ? <p className="mt-5 text-sm text-[#96a0a4]">Стоп-слов пока нет.</p> : (
          <ul className="mt-5 flex flex-wrap gap-2" aria-label="Список стоп-слов">
            {stopwords.map((word) => <li key={word.id} className="inline-flex items-center gap-2 rounded-full border border-[#e5e9ea] bg-[#fafbfb] py-1 pl-3 pr-1 text-sm text-[#516066]">
              {word.word_or_phrase}<IconButton label={`Удалить стоп-слово ${word.word_or_phrase}`} icon={X} onClick={() => void removeStopword(word)} className="h-7 w-7 rounded-full hover:bg-[#fff0ec] hover:text-[#a44739]" />
            </li>)}
          </ul>
        )}
      </section>
    </div>
  );
}
