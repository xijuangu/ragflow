import { useEffect, useId, useRef } from 'react';

export type DialogVariant = 'danger' | 'warning' | 'info';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  variant?: DialogVariant;
  details?: string[];
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmText = '确认',
  cancelText = '取消',
  variant = 'info',
  details = [],
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onCancel();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previousFocus.current?.focus();
    };
  }, [busy, onCancel, open]);

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
    >
      <div
        className={`modal-card confirm-dialog dialog-${variant}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <div className="modal-header">
          <h3 id={titleId}>{title}</h3>
        </div>
        <div className="modal-body">
          <p id={descriptionId} className="dialog-message">{message}</p>
          {details.length > 0 && (
            <ul className="dialog-details">
              {details.map((detail) => <li key={detail}>{detail}</li>)}
            </ul>
          )}
          <div className="modal-actions">
            <button
              ref={cancelRef}
              type="button"
              className="btn btn-outline"
              onClick={onCancel}
              disabled={busy}
            >
              {cancelText}
            </button>
            <button
              type="button"
              className={`btn ${variant === 'danger' ? 'btn-danger dialog-danger-action' : 'btn-primary'}`}
              onClick={onConfirm}
              disabled={busy}
            >
              {busy ? '处理中…' : confirmText}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
