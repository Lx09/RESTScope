/** Keep G6's canvas geometry and its React card layer on the same commit.
 *
 * G6 draws ports and edges on canvas, while `@antv/g6-extension-react` mounts
 * Ant Design cards through a separate React root. React 19's `root.render()`
 * schedules that card work and returns before the DOM commit, so a live update
 * can briefly show new lines attached to an old or absent card. This local G6
 * node preserves the extension's behavior but synchronously commits the card
 * before G6 continues its render queue.
 */

import { ReactNode as AntVReactNode } from "@antv/g6-extension-react";
import { HTML } from "@antv/g6";
import type { ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

type ReactContainer = Element | DocumentFragment;

const roots = new WeakMap<ReactContainer, Root>();

/** Render one G6 card and return only after React has committed its DOM. */
export function renderReactNodeSynchronously(
  node: ReactNode,
  container: ReactContainer,
): void {
  const root = roots.get(container) ?? createRoot(container);
  roots.set(container, root);
  flushSync(() => root.render(node));
}

/** Remove a G6 card synchronously so a destroyed graph leaves no React root. */
export function unmountReactNodeSynchronously(container: ReactContainer): void {
  const root = roots.get(container);
  if (!root) return;
  flushSync(() => root.unmount());
  roots.delete(container);
}

/** Render Ant Design cards as G6 HTML nodes without a line/card race. */
export class SynchronousReactNode extends AntVReactNode {
  public connectedCallback(): void {
    // Calling the HTML base directly deliberately skips the extension's
    // asynchronous React renderer while retaining its DOM and event wiring.
    HTML.prototype.connectedCallback.call(this);
    renderReactNodeSynchronously(
      (this.attributes as unknown as { component: ReactNode }).component,
      this.getDomElement(),
    );
  }

  public attributeChangedCallback(name: unknown, oldValue: unknown, newValue: unknown): void {
    HTML.prototype.attributeChangedCallback.call(this, name, oldValue, newValue);
    if (name === "component" && oldValue !== newValue) {
      renderReactNodeSynchronously(
        (this.attributes as unknown as { component: ReactNode }).component,
        this.getDomElement(),
      );
    }
  }

  public destroy(): void {
    unmountReactNodeSynchronously(this.getDomElement());
    HTML.prototype.destroy.call(this);
  }
}
