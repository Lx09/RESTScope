/** Expose the Main Agent's latest generic Plan as a floating Todo control. */

import { OrderedListOutlined } from "@ant-design/icons";
import { Drawer, FloatButton } from "antd";
import { useState } from "react";

import type { TodoState } from "../types";
import { TodoPanel } from "./TodoPanel";

export function FloatingTodo({
  todo,
  historical,
}: {
  todo: TodoState | null;
  historical: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!todo) return null;

  const remaining = Math.max(0, todo.total_count - todo.completed_count);
  return (
    <>
      <FloatButton
        aria-label={`打开 Todo，已完成 ${todo.completed_count} / ${todo.total_count}`}
        badge={{ count: remaining, overflowCount: 99, showZero: true }}
        className="todo-float-button"
        content={`${todo.completed_count}/${todo.total_count}`}
        icon={<OrderedListOutlined />}
        onClick={() => setOpen(true)}
        shape="square"
        tooltip={`Todo${historical ? " · 历史只读" : ""}`}
      />
      <Drawer
        className="todo-drawer"
        focusable={{ trap: true, focusTriggerAfterClose: true }}
        onClose={() => setOpen(false)}
        open={open}
        placement="right"
        size={520}
        title="Todo"
      >
        <TodoPanel historical={historical} todo={todo} />
      </Drawer>
    </>
  );
}
