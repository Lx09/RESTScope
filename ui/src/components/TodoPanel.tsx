/** Render the Main Agent's latest generic Plan as a read-only Todo list.
 *
 * The panel receives the already-redacted schema-v3 Todo projection and never
 * mutates Agent state. It is shared by the live and historical Drawers; the
 * historical label makes frozen browser data explicit to keyboard and visual
 * users.
 */

import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  OrderedListOutlined,
} from "@ant-design/icons";
import { Empty, Flex, Progress, Space, Tag, Typography } from "antd";

import type { TodoItem, TodoState } from "../types";

const { Paragraph, Text } = Typography;

const STATUS_TEXT: Record<TodoItem["status"], string> = {
  pending: "待处理",
  in_progress: "进行中",
  completed: "已完成",
};

function TodoStatusIcon({ status }: { status: TodoItem["status"] }) {
  if (status === "completed") return <CheckCircleOutlined className="success-icon" />;
  if (status === "in_progress") return <LoadingOutlined className="active-icon" spin />;
  return <ClockCircleOutlined />;
}

export function TodoPanel({
  todo,
  historical = false,
}: {
  todo: TodoState | null;
  historical?: boolean;
}) {
  if (!todo) {
    return (
      <div aria-label="最新 Todo" className="todo-panel">
        <Empty description="尚未更新通用 Plan" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>
    );
  }

  return (
    <div aria-label="最新 Todo" className="todo-panel">
      <Flex align="center" className="todo-meta" gap={8} wrap>
        <OrderedListOutlined />
        <Text strong>Todo</Text>
        {historical && <Tag color="gold">历史 · 只读</Tag>}
        <Tag>Revision {todo.revision}</Tag>
      </Flex>
      <Space className="todo-stack" orientation="vertical" size="middle">
        <div>
          <Flex justify="space-between">
            <Text>已完成 / 总项</Text>
            <Text strong>{todo.completed_count} / {todo.total_count}</Text>
          </Flex>
          <Progress percent={todo.percent} status="active" />
        </div>
        {todo.explanation && <Paragraph className="todo-explanation">{todo.explanation}</Paragraph>}
        <div className="todo-items">
          {todo.items.map((item, index) => (
            <div className={`todo-item todo-item-${item.status}`} key={`${index}:${item.step}`}>
              <TodoStatusIcon status={item.status} />
              <div>
                <div className="todo-step">{item.step}</div>
                <Text type="secondary">{STATUS_TEXT[item.status]}</Text>
              </div>
            </div>
          ))}
        </div>
      </Space>
    </div>
  );
}
