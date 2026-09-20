"use client";

import { use, useEffect, useState, useRef } from "react";
import Link from "next/link";
import {
  Send,
  Bot,
  User as UserIcon,
  Sparkles,
  FileText,
  ArrowRight,
  BadgeCheck,
  AlertTriangle,
  SearchX,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ContractTabs } from "@/components/ui/Badges";
import { AiDisclaimer } from "@/components/ui/AiDisclaimer";
import { errorMessage, fetchContractById, fetchChatHistory, sendChatMessage } from "@/lib/api";
import { sourceHref } from "@/lib/format";
import type { ChatMessage, Citation, ContractDetail } from "@/lib/types";

const SUGGESTED_QUESTIONS = [
  "Can we terminate early?",
  "What is the payment schedule?",
  "When does this agreement expire?",
];

function CitationList({ contractId, citations }: { contractId: string; citations: Citation[] }) {
  return (
    <div className="pt-3 border-t border-slate-100 space-y-2">
      <p className="text-xs font-bold uppercase tracking-wider text-blue-600 flex items-center gap-1.5">
        <FileText className="w-3.5 h-3.5" /> Source Evidence Citations:
      </p>
      <div className="space-y-1.5">
        {citations.map((cite, idx) => (
          <Link
            key={cite.chunk_id ?? idx}
            href={sourceHref(contractId, cite.source_page, cite.snippet)}
            className="block p-2 rounded-lg bg-blue-50/80 hover:bg-blue-100/80 border border-blue-100 text-xs transition-colors group"
          >
            <div className="flex items-center justify-between gap-2 text-blue-700 font-semibold mb-0.5">
              <span>
                Page {cite.source_page} · {cite.source_section}
              </span>
              <span className="flex items-center gap-2">
                {cite.verified ? (
                  <span className="inline-flex items-center gap-1 text-green-700 text-[11px]">
                    <BadgeCheck className="w-3.5 h-3.5" aria-hidden="true" />
                    Verified in document
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-amber-700 text-[11px]">
                    <AlertTriangle className="w-3.5 h-3.5" aria-hidden="true" />
                    Not verified in document
                  </span>
                )}
                <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" aria-hidden="true" />
              </span>
            </div>
            <p className="text-slate-600 italic line-clamp-2">&ldquo;{cite.snippet}&rdquo;</p>
          </Link>
        ))}
      </div>
    </div>
  );
}

function AssistantBody({ msg, contractId }: { msg: ChatMessage; contractId: string }) {
  const citations = msg.citations ?? [];

  if (msg.answer_status === "not_found") {
    return (
      <div className="space-y-1">
        <p className="flex items-center gap-2 font-semibold text-slate-700">
          <SearchX className="w-4 h-4 text-slate-500" aria-hidden="true" />
          Not found in this contract
        </p>
        {msg.content && <p className="text-xs text-slate-500 whitespace-pre-wrap">{msg.content}</p>}
      </div>
    );
  }

  return (
    <>
      {msg.answer_status === "unavailable" && (
        <p className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-100 border border-amber-200 text-amber-800 text-[11px] font-bold">
          <AlertTriangle className="w-3 h-3" aria-hidden="true" />
          Search results — no AI answer
        </p>
      )}
      <p className="whitespace-pre-wrap">{msg.content}</p>
      {citations.length > 0 && <CitationList contractId={contractId} citations={citations} />}
    </>
  );
}

export default function ChatPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [contract, setContract] = useState<ContractDetail | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    async function loadData() {
      try {
        const [cData, chatData] = await Promise.all([fetchContractById(id), fetchChatHistory(id)]);
        setContract(cData);
        setMessages(chatData);
      } catch (err: unknown) {
        setLoadError(errorMessage(err, "Could not load the chat."));
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [id]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  // The free AI plan is rate-limited, so an answer can occasionally take up to a minute.
  // Say so instead of leaving the user staring at a spinner wondering if it broke.
  useEffect(() => {
    if (!sending) {
      setSlow(false);
      return;
    }
    const t = setTimeout(() => setSlow(true), 8000);
    return () => clearTimeout(t);
  }, [sending]);

  const ready = contract?.status === "READY";
  const notReadyReason = !contract
    ? null
    : contract.status === "READY"
      ? null
      : contract.status === "PENDING" || contract.status === "PROCESSING"
        ? "This contract is still being processed. You can ask questions once it is ready."
        : "Questions are unavailable because this contract could not be processed.";

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || sending || !ready) return;

    const userText = input.trim();
    setInput("");
    setSendError(null);
    setSending(true);

    // Optimistically append User Message
    const tempId = "temp-" + Date.now();
    const tempUserMsg: ChatMessage = {
      id: tempId,
      role: "USER",
      content: userText,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    try {
      const assistantResponse = await sendChatMessage(id, userText);
      setMessages((prev) => [...prev, assistantResponse]);
    } catch (err: unknown) {
      // Roll back the optimistic message and give the question back to the user.
      setMessages((prev) => prev.filter((m) => m.id !== tempId));
      setInput(userText);
      setSendError(errorMessage(err, "Your question could not be sent."));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-slate-50">
      <PageHeader
        title={contract ? `${contract.title} — Ask` : "Ask this contract"}
        subtitle="Ask a question and inspect the source evidence behind the answer"
        breadcrumbs={[
          { label: "Dashboard", href: "/" },
          { label: contract?.title || "Contract", href: `/contracts/${id}` },
          { label: "Ask" },
        ]}
      />
      <ContractTabs contractId={id} active="chat" />

      {/* Main Chat Container */}
      <div className="flex-1 flex flex-col min-h-0 p-3 sm:p-6 max-w-5xl w-full mx-auto">
        <div className="flex-1 overflow-y-auto space-y-4 sm:pr-2">
          {loading ? (
            <div className="p-8 text-center text-sm text-slate-400">Loading chat history...</div>
          ) : loadError ? (
            <div role="alert" className="p-8 text-center text-sm text-red-600">
              {loadError}
            </div>
          ) : messages.length === 0 ? (
            <div className="p-8 text-center space-y-3 bg-white rounded-2xl border border-slate-200 shadow-sm">
              <div className="w-12 h-12 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center mx-auto">
                <Sparkles className="w-6 h-6" />
              </div>
              <h3 className="text-base font-bold text-slate-800">
                Ask anything about {contract?.title || "this contract"}
              </h3>
              <p className="text-xs text-slate-500 max-w-md mx-auto">
                Answers are drawn only from the contract text, with page & section citations. If the contract does not
                say, the assistant will tell you it was not found.
              </p>
              <div className="flex flex-wrap justify-center gap-2 pt-2">
                {SUGGESTED_QUESTIONS.map((sample) => (
                  <button
                    key={sample}
                    type="button"
                    onClick={() => setInput(sample)}
                    disabled={!ready}
                    className="text-xs font-semibold px-3 py-1.5 bg-slate-100 hover:bg-blue-50 hover:text-blue-600 disabled:opacity-50 disabled:hover:bg-slate-100 disabled:hover:text-slate-600 text-slate-600 rounded-xl transition-colors"
                  >
                    &ldquo;{sample}&rdquo;
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => {
              const notFound = msg.role === "ASSISTANT" && msg.answer_status === "not_found";
              return (
                <div key={msg.id} className={`flex gap-3 ${msg.role === "USER" ? "justify-end" : "justify-start"}`}>
                  {msg.role === "ASSISTANT" && (
                    <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center shrink-0 shadow-sm mt-1">
                      <Bot className="w-4 h-4" />
                    </div>
                  )}

                  <div
                    className={`min-w-0 max-w-2xl break-words rounded-2xl p-4 text-sm leading-relaxed shadow-sm space-y-3 ${
                      msg.role === "USER"
                        ? "bg-gradient-to-r from-blue-600 to-indigo-600 text-white font-medium"
                        : notFound
                          ? "bg-slate-100 border border-slate-300 text-slate-700"
                          : msg.answer_status === "unavailable"
                            ? "bg-white border border-amber-300 text-slate-800"
                            : "bg-white border border-slate-200 text-slate-800"
                    }`}
                  >
                    {msg.role === "USER" ? (
                      <p className="whitespace-pre-wrap">{msg.content}</p>
                    ) : (
                      <AssistantBody msg={msg} contractId={id} />
                    )}
                  </div>

                  {msg.role === "USER" && (
                    <div className="w-8 h-8 rounded-full bg-slate-800 text-white flex items-center justify-center shrink-0 shadow-sm mt-1">
                      <UserIcon className="w-4 h-4" />
                    </div>
                  )}
                </div>
              );
            })
          )}

          {sending && (
            <div className="flex gap-3 justify-start">
              <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center shrink-0 animate-pulse">
                <Bot className="w-4 h-4" />
              </div>
              <div className="bg-white border border-slate-200 rounded-2xl p-4 text-xs font-semibold text-slate-500 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-blue-500 animate-spin" />
                <span>
                  Searching the contract and preparing an answer...
                  {slow && (
                    <span className="block font-normal text-slate-400 mt-0.5">
                      Still working. The free AI plan is rate-limited, so this can take up to a minute.
                    </span>
                  )}
                </span>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        {notReadyReason && (
          <p role="status" className="mt-4 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">
            {notReadyReason}
          </p>
        )}
        {sendError && (
          <p role="alert" className="mt-4 text-xs text-red-800 bg-red-50 border border-red-200 rounded-xl px-3 py-2">
            {sendError}
          </p>
        )}
        <form onSubmit={handleSend} className="mt-4 flex gap-2">
          <label htmlFor="chat-input" className="sr-only">
            Ask a question about this contract
          </label>
          <input
            id="chat-input"
            type="text"
            value={input}
            maxLength={2000}
            disabled={!ready}
            onChange={(e) => setInput(e.target.value)}
            placeholder={ready ? "Ask a question about this contract..." : "Questions are unavailable until the contract is ready"}
            className="flex-1 px-4 py-3 bg-white border border-slate-300 rounded-2xl text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 shadow-sm disabled:bg-slate-100"
          />
          <button
            type="submit"
            disabled={!input.trim() || sending || !ready}
            className="px-5 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 disabled:opacity-50 text-white font-semibold rounded-2xl text-sm shadow-md transition-all flex items-center gap-2"
          >
            <Send className="w-4 h-4" />
            Send
          </button>
        </form>
        <AiDisclaimer className="mt-3 rounded-xl border" />
      </div>
    </div>
  );
}
