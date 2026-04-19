"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  text: string;
  className?: string;
};

/** Render markdown for chat bubbles. Tight spacing, inline `code`,
 *  fenced code blocks with horizontal scroll, and GFM tables/task-lists.
 *  Links open in a new tab. Safe for streamed partial text. */
export function Markdown({ text, className }: Props) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="my-1 leading-relaxed">{children}</p>,
          ul: ({ children }) => (
            <ul className="my-1 ml-5 list-disc space-y-0.5">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="my-1 ml-5 list-decimal space-y-0.5">{children}</ol>
          ),
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          h1: ({ children }) => (
            <h1 className="text-base font-semibold mt-2 mb-1">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-sm font-semibold mt-2 mb-1">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-semibold mt-2 mb-1">{children}</h3>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2 hover:text-primary"
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-border pl-3 my-1 text-muted-foreground">
              {children}
            </blockquote>
          ),
          code: ({
            inline,
            className,
            children,
            ...rest
          }: {
            inline?: boolean;
            className?: string;
            children?: React.ReactNode;
          } & React.HTMLAttributes<HTMLElement>) => {
            if (inline) {
              return (
                <code
                  className="bg-muted px-1 py-0.5 rounded text-[0.85em] font-mono"
                  {...rest}
                >
                  {children}
                </code>
              );
            }
            return (
              <code className={`${className ?? ""} font-mono text-[0.85em]`} {...rest}>
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre className="bg-muted rounded-md p-2.5 my-2 overflow-x-auto text-[0.85em]">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="my-2 overflow-x-auto">
              <table className="border-collapse text-[0.9em]">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-border px-2 py-1 text-left font-semibold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-border px-2 py-1">{children}</td>
          ),
          hr: () => <hr className="my-2 border-border" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
