import type { ReactNode } from 'react';

interface MobileCardField {
  label: string;
  value: ReactNode;
}

export function MobileCardList({
  label,
  children,
  className = '',
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mobile-cards${className ? ` ${className}` : ''}`} role="list" aria-label={label}>
      {children}
    </div>
  );
}

export function MobileCard({
  title,
  badge,
  fields,
  actions,
  testId,
}: {
  title: ReactNode;
  badge?: ReactNode;
  fields: MobileCardField[];
  actions?: ReactNode;
  testId?: string;
}) {
  return (
    <article className="m-card" role="listitem" data-testid={testId}>
      <div className="m-card-head">
        <div className="ttl">{title}</div>
        {badge}
      </div>
      <dl className="m-card-fields">
        {fields.map((field) => (
          <div className="m-card-field" key={field.label}>
            <dt>{field.label}</dt>
            <dd>{field.value}</dd>
          </div>
        ))}
      </dl>
      {actions && <div className="m-card-actions">{actions}</div>}
    </article>
  );
}
