"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { useHoldingLists } from "@/hooks/useHoldingLists";
import {
  ASSET_CLASSES,
  type AssetClass,
  type ManualPosition,
  type WatchTicker,
} from "@/lib/contract";
import { SYMBOL_PATTERN, normaliseSymbol, type WriteResult } from "@/lib/data";

/** What each asset class is called in the editor. */
const CLASS_LABEL: Readonly<Record<AssetClass, string>> = {
  equity: "Stock",
  etf: "ETF",
  crypto: "Crypto",
  option: "Option",
  commodity: "Commodity",
  fixed_income: "Bond",
  other: "Other",
};

/**
 * The operator's two short lists, tucked behind a disclosure: positions at a
 * broker Accounting does not connect to, and names watched without being
 * held. Each write goes through one of the four SECURITY DEFINER functions in
 * migration 20260924000400; a row is removed by deactivating it, never
 * deleted. The lists are read only once the disclosure is opened.
 */
export function HoldingsEditor() {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="hold-manage"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Manage list</summary>
      {open ? <Lists /> : null}
    </details>
  );
}

function Lists() {
  const lists = useHoldingLists(true);
  return (
    <div className="hold-manage-body">
      <p className="muted">
        Your Tiger and Hyperliquid positions come in from Accounting on their
        own. Add a name here only if you hold it at a broker Accounting
        can&apos;t see, or want to follow it without holding it. Changes show
        from the next holdings watch, at 09:05.
      </p>
      {lists.readError === null ? null : (
        <p className="desk-refused small">{lists.readError}</p>
      )}
      <ListBlock
        addLabel="Add a position"
        empty="Nothing held elsewhere."
        loading={lists.loading}
        onRetire={(id) => lists.retireManual.mutateAsync(id)}
        onSaveNote={(row, note) =>
          lists.saveManual.mutateAsync({ ...manualInput(row as ManualPosition), note })
        }
        rows={lists.manual}
        title="Held elsewhere"
        withVenue
        onAdd={(draft) =>
          lists.saveManual.mutateAsync({
            id: null,
            symbol: draft.symbol,
            name: draft.name,
            venue: draft.venue,
            assetClass: draft.assetClass,
            quantity: null,
            note: draft.note,
          })
        }
      />
      <ListBlock
        addLabel="Watch a name"
        empty="Not watching anything you don't hold."
        loading={lists.loading}
        onRetire={(id) => lists.retireWatch.mutateAsync(id)}
        onSaveNote={(row, note) =>
          lists.saveWatch.mutateAsync({ ...watchInput(row as WatchTicker), note })
        }
        rows={lists.watch}
        title="Watching"
        onAdd={(draft) =>
          lists.saveWatch.mutateAsync({
            id: null,
            symbol: draft.symbol,
            name: draft.name,
            assetClass: draft.assetClass,
            note: draft.note,
          })
        }
      />
    </div>
  );
}

function manualInput(row: ManualPosition) {
  return {
    id: row.id,
    symbol: row.symbol,
    name: row.name,
    venue: row.venue,
    assetClass: row.assetClass,
    quantity: row.quantity,
    note: row.note,
  };
}

function watchInput(row: WatchTicker) {
  return {
    id: row.id,
    symbol: row.symbol,
    name: row.name,
    assetClass: row.assetClass,
    note: row.note,
  };
}

interface Draft {
  symbol: string;
  name: string | null;
  venue: string;
  assetClass: AssetClass;
  note: string | null;
}

type Row = ManualPosition | WatchTicker;

function ListBlock({
  title,
  rows,
  empty,
  loading,
  withVenue = false,
  addLabel,
  onAdd,
  onSaveNote,
  onRetire,
}: {
  title: string;
  rows: readonly Row[];
  empty: string;
  loading: boolean;
  withVenue?: boolean;
  addLabel: string;
  onAdd: (draft: Draft) => Promise<WriteResult>;
  onSaveNote: (row: Row, note: string | null) => Promise<WriteResult>;
  onRetire: (id: string) => Promise<WriteResult>;
}) {
  const [adding, setAdding] = useState(false);
  // After Add or Cancel the form is gone; focus goes back to the button that
  // opened it.
  const [restoreAdd, setRestoreAdd] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const sorted = [...rows].sort((a, b) => a.symbol.localeCompare(b.symbol));
  // A removed row takes its buttons with it. Focus goes to the list's own add
  // button rather than falling to the page, which a keyboard user would then
  // have to find their way back from.
  const focusList = () => (addButton.current ?? heading.current)?.focus();
  useEffect(() => {
    if (adding || !restoreAdd) return;
    addButton.current?.focus();
    setRestoreAdd(false);
  }, [adding, restoreAdd]);
  return (
    <div className="hold-manage-list">
      <h4 ref={heading} tabIndex={-1}>
        {title}
      </h4>
      {loading ? (
        <p className="faint small">Reading the list…</p>
      ) : sorted.length === 0 ? (
        <p className="faint small">{empty}</p>
      ) : (
        <ul>
          {sorted.map((row) => (
            <ListRow
              key={row.id}
              onRemoved={focusList}
              onRetire={onRetire}
              onSaveNote={onSaveNote}
              row={row}
            />
          ))}
        </ul>
      )}
      {adding ? (
        <AddForm
          onCancel={() => {
            setAdding(false);
            setRestoreAdd(true);
          }}
          onSubmit={async (draft) => {
            const result = await onAdd(draft);
            if (result.ok) {
              setAdding(false);
              setRestoreAdd(true);
            }
            return result;
          }}
          withVenue={withVenue}
        />
      ) : (
        <button
          className="button-plain"
          onClick={() => setAdding(true)}
          ref={addButton}
          type="button"
        >
          + {addLabel}
        </button>
      )}
    </div>
  );
}

function ListRow({
  row,
  onSaveNote,
  onRetire,
  onRemoved,
}: {
  row: Row;
  onSaveNote: (row: Row, note: string | null) => Promise<WriteResult>;
  onRetire: (id: string) => Promise<WriteResult>;
  /** Called once the row is gone, to put focus somewhere that still exists. */
  onRemoved: () => void;
}) {
  const [mode, setMode] = useState<"view" | "note" | "remove">("view");
  const [note, setNote] = useState(row.note ?? "");
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  // Which button focus returns to when the row goes back to view: the one
  // that opened the note form or the question.
  const [returnTo, setReturnTo] = useState<"note" | "remove" | null>(null);
  const noteButton = useRef<HTMLButtonElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const venue = "venue" in row ? row.venue : null;

  useEffect(() => {
    if (mode !== "view" || returnTo === null) return;
    (returnTo === "note" ? noteButton : removeButton).current?.focus();
    setReturnTo(null);
  }, [mode, returnTo]);

  const back = (from: "note" | "remove") => {
    setReturnTo(from);
    setMode("view");
  };

  const run = async (
    from: "note" | "remove",
    write: () => Promise<WriteResult>,
  ) => {
    setBusy(true);
    setRefused(null);
    try {
      const result = await write();
      if (!result.ok) setRefused(result.reason);
      else if (from === "remove") {
        setMode("view");
        onRemoved();
      } else back(from);
    } catch (cause) {
      setRefused(cause instanceof Error ? cause.message : "The write failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="hold-manage-row">
      <div className="hold-manage-line">
        <b className="hold-symbol">{row.symbol}</b>
        {row.name === null ? null : <span className="hold-name">{row.name}</span>}
        <span className="faint small">
          {[venue, CLASS_LABEL[row.assetClass]].filter(Boolean).join(" · ")}
        </span>
        {mode === "view" ? (
          <span className="hold-manage-actions">
            <button
              className="button-plain"
              onClick={() => {
                setNote(row.note ?? "");
                setMode("note");
              }}
              ref={noteButton}
              type="button"
            >
              Edit note
            </button>
            <button
              className="button-plain"
              onClick={() => setMode("remove")}
              ref={removeButton}
              type="button"
            >
              Remove
            </button>
          </span>
        ) : null}
      </div>
      {mode === "view" && row.note !== null ? (
        <p className="faint small hold-manage-note">{row.note}</p>
      ) : null}
      {mode === "note" ? (
        <form
          className="hold-manage-inline"
          onSubmit={(event) => {
            event.preventDefault();
            void run("note", () =>
              onSaveNote(row, note.trim() === "" ? null : note),
            );
          }}
        >
          <input
            aria-label={`Note for ${row.symbol}`}
            autoFocus
            maxLength={200}
            onChange={(event) => setNote(event.target.value)}
            placeholder="A short note, or leave it blank"
            value={note}
          />
          <button className="button-accent" disabled={busy} type="submit">
            Save
          </button>
          <button
            className="button-plain"
            onClick={() => back("note")}
            type="button"
          >
            Cancel
          </button>
        </form>
      ) : null}
      {mode === "remove" ? (
        <div className="hold-manage-inline">
          <span className="small">
            {venue === null
              ? `Stop watching ${row.symbol}?`
              : `Remove ${row.symbol} from your held-elsewhere list?`}{" "}
            It stays on record.
          </span>
          <button
            autoFocus
            className="button-accent"
            disabled={busy}
            onClick={() => void run("remove", () => onRetire(row.id))}
            type="button"
          >
            Remove
          </button>
          <button
            className="button-plain"
            onClick={() => back("remove")}
            type="button"
          >
            Keep
          </button>
        </div>
      ) : null}
      {refused === null ? null : (
        <p className="desk-refused small">{refused}</p>
      )}
    </li>
  );
}

function AddForm({
  withVenue,
  onSubmit,
  onCancel,
}: {
  withVenue: boolean;
  onSubmit: (draft: Draft) => Promise<WriteResult>;
  onCancel: () => void;
}) {
  const [symbol, setSymbol] = useState("");
  const [name, setName] = useState("");
  const [venue, setVenue] = useState("");
  const [assetClass, setAssetClass] = useState<AssetClass>("equity");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);

  const cleanSymbol = normaliseSymbol(symbol);
  const symbolOk = SYMBOL_PATTERN.test(cleanSymbol);
  const ready = symbolOk && (!withVenue || venue.trim() !== "");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setRefused(null);
    try {
      const result = await onSubmit({
        symbol: cleanSymbol,
        name: name.trim() === "" ? null : name,
        venue,
        assetClass,
        note: note.trim() === "" ? null : note,
      });
      if (!result.ok) setRefused(result.reason);
    } catch (cause) {
      setRefused(cause instanceof Error ? cause.message : "The write failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="hold-manage-form" onSubmit={submit}>
      <label className="field">
        <span>Symbol</span>
        <input
          autoCapitalize="characters"
          autoFocus
          maxLength={24}
          onChange={(event) => setSymbol(event.target.value)}
          placeholder={withVenue ? "D05" : "MU"}
          value={symbol}
        />
      </label>
      <label className="field">
        <span>Name (optional)</span>
        <input
          maxLength={80}
          onChange={(event) => setName(event.target.value)}
          placeholder={withVenue ? "DBS Group" : "Micron Technology"}
          value={name}
        />
      </label>
      {withVenue ? (
        <label className="field">
          <span>Broker</span>
          <input
            maxLength={40}
            onChange={(event) => setVenue(event.target.value)}
            placeholder="DBS Vickers"
            value={venue}
          />
        </label>
      ) : null}
      <label className="field">
        <span>Type</span>
        <select
          onChange={(event) => setAssetClass(event.target.value as AssetClass)}
          value={assetClass}
        >
          {ASSET_CLASSES.map((one) => (
            <option key={one} value={one}>
              {CLASS_LABEL[one]}
            </option>
          ))}
        </select>
      </label>
      <label className="field hold-manage-wide">
        <span>Note (optional)</span>
        <input
          maxLength={200}
          onChange={(event) => setNote(event.target.value)}
          placeholder={withVenue ? "SGX, in the CDP account" : "Why you follow it"}
          value={note}
        />
      </label>
      <p className="faint small hold-manage-wide">
        {symbol !== "" && !symbolOk
          ? "A symbol is letters, digits and . ^ = / : - only, up to 24 characters."
          : "Use the exchange's own code. A non-US listing can also take its Yahoo symbol, such as D05.SI."}
      </p>
      <div className="desk-refresh-actions hold-manage-wide">
        <button className="button-accent" disabled={busy || !ready} type="submit">
          Add
        </button>
        <button className="button-plain" onClick={onCancel} type="button">
          Cancel
        </button>
      </div>
      {refused === null ? null : (
        <p className="desk-refused small hold-manage-wide">{refused}</p>
      )}
    </form>
  );
}
