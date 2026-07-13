import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';

interface InputDialogProps {
  open: boolean;
  title: string;
  label: string;
  initialValue?: string;
  confirmText?: string;
  cancelText?: string;
  busy?: boolean;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export default function InputDialog({
  open,
  title,
  label,
  initialValue = '',
  confirmText = '保存',
  cancelText = '取消',
  busy = false,
  onConfirm,
  onCancel,
}: InputDialogProps) {
  const titleId = useId();
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [value, setValue] = useState(initialValue);

  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement as HTMLElement | null;
    setValue(initialValue);
    inputRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onCancel();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previousFocus.current?.focus();
    };
  }, [busy, initialValue, onCancel, open]);

  if (!open) return null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const nextValue = value.trim();
    if (nextValue && !busy) onConfirm(nextValue);
  };

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
    >
      <form
        className="modal-card input-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={submit}
      >
        <div className="modal-header">
          <h3 id={titleId}>{title}</h3>
        </div>
        <div className="modal-body">
          <div className="form-field">
            <label htmlFor={inputId}>{label}</label>
            <input
              ref={inputRef}
              id={inputId}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              disabled={busy}
              required
            />
          </div>
          <div className="modal-actions">
            <button type="button" className="btn btn-outline" onClick={onCancel} disabled={busy}>
              {cancelText}
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy || !value.trim()}>
              {busy ? '保存中…' : confirmText}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
