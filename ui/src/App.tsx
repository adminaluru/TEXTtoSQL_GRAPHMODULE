import { useCallback, useMemo, useState } from "react";

import { createClient, type ApiClient, type Caller } from "./api/client";
import type { MeResponse } from "./api/types";
import { ChatPanel } from "./components/ChatPanel";
import { DocumentsPanel } from "./components/DocumentsPanel";
import { EvidencePanel } from "./components/EvidencePanel";
import { Sidebar } from "./components/Sidebar";
import { SignIn } from "./components/SignIn";
import { TopBar } from "./components/TopBar";
import { humanize } from "./format/values";
import { useChat } from "./state/useChat";
import { useDocuments } from "./state/useDocuments";

/**
 * Composition root of the UI: holds who is signed in (token in memory only),
 * the active workspace, and wires the panels together.
 */
export function App({ api = createClient() }: { api?: ApiClient }) {
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [tenant, setTenant] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const caller: Caller | null = useMemo(() => (token ? { token, tenant } : null), [token, tenant]);

  const signOut = useCallback((message: string | null = null) => {
    setToken(null);
    setMe(null);
    setTenant(null);
    setSelectedId(null);
    setNotice(message);
  }, []);

  const handlers = useMemo(
    () => ({
      onUnauthenticated: () => signOut("Your session has ended. Please sign in again."),
      // Membership may have changed: refresh the list of workspaces.
      onForbidden: () => {
        if (!token) return;
        api.me({ token, tenant: null }).then(
          (fresh) => {
            setMe(fresh);
            if (tenant && !fresh.tenants.includes(tenant)) setTenant(fresh.tenants.length === 1 ? fresh.tenants[0] : null);
          },
          () => signOut("Your session has ended. Please sign in again."),
        );
      },
    }),
    [api, signOut, tenant, token],
  );

  const chat = useChat(api, caller, handlers);
  const docs = useDocuments(api, caller, handlers);

  const changeTenant = (next: string) => {
    if (next === tenant) return;
    chat.resetForTenantChange();
    setSelectedId(null);
    setTenant(next || null);
  };

  if (!token || !me) {
    return (
      <SignIn
        api={api}
        notice={notice}
        onSignedIn={(value, profile) => {
          setToken(value);
          setMe(profile);
          setNotice(null);
          setTenant(profile.tenants.length === 1 ? profile.tenants[0] : null);
        }}
      />
    );
  }

  const messages = chat.active?.messages ?? [];
  const selected = messages.find((m) => m.id === selectedId && m.role === "assistant")
    ?? [...messages].reverse().find((m) => m.role === "assistant")
    ?? null;
  const roleLabel = me.roles.length ? me.roles.map(humanize).join(", ") : "No role assigned";

  return (
    <div className="app">
      <TopBar
        tenants={me.tenants}
        tenant={tenant}
        subject={me.subject_id}
        onTenantChange={changeTenant}
        onSignOut={() => signOut()}
      />
      <div className="body">
        <Sidebar
          conversations={chat.conversations}
          activeId={chat.active?.id ?? null}
          listStatus={chat.listStatus}
          listError={chat.listError}
          roleLabel={roleLabel}
          subject={me.subject_id}
          onNew={() => {
            chat.startConversation();
            setSelectedId(null);
          }}
          onSelect={(id) => {
            chat.selectConversation(id);
            setSelectedId(null);
          }}
          onDelete={(id) => {
            void chat.removeConversation(id);
            setSelectedId(null);
          }}
        />
        {tenant ? (
          <ChatPanel
            conversation={chat.active}
            busy={chat.busy}
            canAsk={!!tenant}
            selectedMessageId={selected?.id ?? null}
            onSelectMessage={setSelectedId}
            onSend={(q) => void chat.send(q)}
            onChoose={(mid, cid, oid, label) => void chat.choose(mid, cid, oid, label)}
          />
        ) : (
          <main className="panel">
            <div className="empty-state">
              <p><strong>Choose a workspace</strong></p>
              <p>You have access to several companies. Pick one at the top to start.</p>
            </div>
          </main>
        )}
        <EvidencePanel
          answer={selected?.answer ?? null}
          answeredAt={selected?.at ?? null}
          documents={
            <DocumentsPanel
              enabled={!!tenant}
              documents={docs.documents}
              loaded={docs.loaded}
              uploading={docs.uploading}
              busyIds={docs.busyIds}
              error={docs.error}
              notice={docs.notice}
              search={docs.search}
              searching={docs.searching}
              onSearch={(q) => void docs.find(q)}
              onClearSearch={docs.clearSearch}
              onUpload={(file) => void docs.upload(file)}
              onRetry={(id) => void docs.retry(id)}
              onDelete={(id) => void docs.remove(id)}
            />
          }
        />
      </div>
    </div>
  );
}
