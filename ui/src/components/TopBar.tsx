interface Props {
  tenants: string[];
  tenant: string | null;
  subject: string;
  onTenantChange: (tenant: string) => void;
  onSignOut: () => void;
}

/** Brand, workspace (company) switcher and the signed-in user. */
export function TopBar({ tenants, tenant, subject, onTenantChange, onSignOut }: Props) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        FlowOne <small>Hybrid AI</small>
      </div>

      <div className="workspace">
        <label htmlFor="workspace">Workspace</label>
        <select
          id="workspace"
          value={tenant ?? ""}
          onChange={(e) => onTenantChange(e.target.value)}
          disabled={tenants.length < 2}
        >
          {!tenant && <option value="">Choose…</option>}
          {tenants.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>

      <div className="user">
        <span>{subject}</span>
        <button type="button" className="link-button" onClick={onSignOut}>Sign out</button>
      </div>
    </header>
  );
}
