/** Safe structured-data viewers for prompts, tools, HTTP bodies, and headers. */

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import { Button, Tag, Tooltip } from "antd";
import { useState } from "react";

interface CodeViewProps {
  value: unknown;
  label?: string;
}

export function CodeView({ value, label = "复制内容" }: CodeViewProps) {
  const [copied, setCopied] = useState(false);
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);

  async function copy() {
    if (!navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(text ?? "");
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <div className="code-view">
      <Tooltip title={copied ? "已复制" : label}>
        <Button
          aria-label={label}
          className="copy-button"
          icon={copied ? <CheckOutlined /> : <CopyOutlined />}
          onClick={() => void copy()}
          size="small"
          type="text"
        />
      </Tooltip>
      <pre>{text ?? "null"}</pre>
    </div>
  );
}

export function HeaderTable({ headers }: { headers: Record<string, unknown> | null }) {
  const rows = Object.entries(headers ?? {});
  if (!rows.length) return <span className="muted">无 headers</span>;
  return (
    <div className="table-scroll">
      <table className="header-table">
        <thead>
          <tr>
            <th scope="col">Header</th>
            <th scope="col">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, value]) => (
            <tr key={name}>
              <th scope="row">{name}</th>
              <td>{String(value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface BodyValue {
  format?: "json" | "text" | "base64";
  value?: unknown;
}

export function BodyView({ body }: { body: BodyValue | null | undefined }) {
  if (!body) return <span className="muted">无正文</span>;
  return (
    <div>
      <Tag className="format-tag">{body.format ?? "unknown"}</Tag>
      {body.format === "base64" && (
        <div className="binary-note">二进制正文 · Base64 编码显示</div>
      )}
      <CodeView value={body.value} label="复制正文" />
    </div>
  );
}
