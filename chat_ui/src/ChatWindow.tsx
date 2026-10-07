import React, { useState, useRef, useEffect } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// Tailwind styling for markdown elements in bot replies (tables, lists, emphasis)
const markdownComponents: Components = {
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-gray-900">{children}</strong>,
  ul: ({ children }) => <ul className="my-2 list-disc pl-5 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal pl-5 space-y-1">{children}</ol>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-blue-600 underline">{children}</a>
  ),
  code: ({ children }) => <code className="rounded-sm bg-gray-100 px-1 py-0.5 font-mono text-xs">{children}</code>,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-lg border border-gray-200">
      <table className="min-w-full text-xs">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-gray-50 text-gray-600">{children}</thead>,
  tr: ({ children }) => <tr className="border-b border-gray-100 last:border-0 even:bg-gray-50/50">{children}</tr>,
  th: ({ children }) => <th className="px-3 py-2 text-left font-semibold whitespace-nowrap">{children}</th>,
  td: ({ children }) => <td className="px-3 py-1.5 whitespace-nowrap tabular-nums">{children}</td>,
};

// Safety net in case the model echoes the raw tool wrapper tags or data block
const cleanBotText = (text: string) =>
  text.replace(/<data>[\s\S]*?(<\/data>|$)/g, '').replace(/<\/?display>/g, '').trim();

interface Message {
  id: string;
  sender: 'user' | 'bot';
  text: string;
  statusLogs?: string[];
}

export const ChatWindow: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [currentStatus, setCurrentStatus] = useState<string | null>(null);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // One conversation id per page load so the backend can keep chat history
  const threadIdRef = useRef<string>(crypto.randomUUID());

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, currentStatus]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessageText = input;
    setInput('');
    setIsLoading(true);

    const userMsgId = crypto.randomUUID();
    const botMsgId = crypto.randomUUID();

    setMessages((prev) => [
      ...prev,
      { id: userMsgId, sender: 'user', text: userMessageText },
      { id: botMsgId, sender: 'bot', text: '', statusLogs: [] },
    ]);

    try {
      const response = await fetch(`${API_URL}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMessageText, thread_id: threadIdRef.current }),
      });

      if (!response.ok) throw new Error(`Server responded with status ${response.status}.`);
      if (!response.body) throw new Error('Readable stream missing.');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      // Holds a partial line when an event is split across network chunks
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            let data: { type: string; content: string };
            try {
              data = JSON.parse(line.slice(6));
            } catch {
              console.warn('Skipping malformed stream event:', line);
              continue;
            }

            if (data.type === 'text') {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === botMsgId ? { ...msg, text: msg.text + data.content } : msg
                )
              );
            } else if (data.type === 'status') {
              setCurrentStatus(data.content);
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === botMsgId
                    ? { ...msg, statusLogs: [...(msg.statusLogs || []), data.content] }
                    : msg
                )
              );
            } else if (data.type === 'error') {
              console.error(data.content);
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === botMsgId
                    ? { ...msg, text: (msg.text ? msg.text + '\n\n' : '') + '⚠️ ' + data.content }
                    : msg
                )
              );
            }
          }
        }
      }
    } catch (error) {
      console.error('Failed to stream response:', error);
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === botMsgId ? { ...msg, text: 'An error occurred during processing.' } : msg
        )
      );
    } finally {
      setIsLoading(false);
      setCurrentStatus(null);
    }
  };

  return (
    <div className="flex flex-col h-screen max-w-3xl mx-auto border border-gray-200 bg-gray-50 shadow-md">
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-800">Customer Support Bot</h1>
          <p className="text-xs text-gray-500">Sales Policies (LlamaIndex) + Client Loans (MCP)</p>
        </div>
        <div className="flex items-center space-x-2">
          <span className="w-2.5 h-2.5 bg-green-500 rounded-full animate-pulse" />
          <span className="text-sm font-medium text-gray-600">Active Pipeline</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {messages.map((msg) => {
          const isUser = msg.sender === 'user';
          return (
            <div key={msg.id} className={`flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
              {!isUser && msg.statusLogs && msg.statusLogs.length > 0 && (
                <div className="mb-1 flex flex-col space-y-0.5 max-w-md">
                  {Array.from(new Set(msg.statusLogs)).map((log, idx) => (
                    <span key={idx} className="text-[11px] font-mono text-gray-400 bg-gray-100 px-2 py-0.5 rounded-sm">
                      ⚙️ {log}
                    </span>
                  ))}
                </div>
              )}

              {isUser ? (
                <div className="max-w-md px-4 py-2.5 rounded-2xl rounded-br-none text-sm leading-relaxed whitespace-pre-wrap bg-blue-600 text-white">
                  {msg.text}
                </div>
              ) : (
                <div className="max-w-full px-4 py-2.5 rounded-2xl rounded-bl-none text-sm leading-relaxed bg-white text-gray-800 border border-gray-200 shadow-xs">
                  {msg.text ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                      {cleanBotText(msg.text)}
                    </ReactMarkdown>
                  ) : (
                    '...'
                  )}
                </div>
              )}
            </div>
          );
        })}

        {currentStatus && (
          <div className="flex items-center space-x-2 text-xs italic text-blue-500 bg-blue-50 border border-blue-100 px-3 py-1.5 rounded-md animate-pulse max-w-max">
            <span>{currentStatus}</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSendMessage} className="p-4 bg-white border-t border-gray-200 flex space-x-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a policy question or about a client's loans..."
          disabled={isLoading}
          className="flex-1 px-4 py-2 text-sm border border-gray-300 rounded-xl focus:outline-hidden focus:ring-2 focus:ring-blue-500"
        />
        <button type="submit" disabled={isLoading || !input.trim()} className="bg-blue-600 text-white px-5 py-2 text-sm rounded-xl font-medium">
          Send
        </button>
      </form>
    </div>
  );
};
