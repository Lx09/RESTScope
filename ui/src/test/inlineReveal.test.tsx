import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DETAIL_CLOSE_DURATION_MS,
  DETAIL_OPEN_DURATION_MS,
  InlineReveal,
} from "../components/InlineReveal";

interface ControlledAnimation {
  cancel: Animation["cancel"];
  onfinish: Animation["onfinish"];
}

const animations: ControlledAnimation[] = [];
let originalAnimate: typeof Element.prototype.animate | undefined;
let originalMatchMedia: typeof window.matchMedia;

beforeEach(() => {
  animations.length = 0;
  originalAnimate = Element.prototype.animate;
  originalMatchMedia = window.matchMedia;
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    value: vi.fn(() => {
      const animation: ControlledAnimation = {
        cancel: vi.fn(),
        onfinish: null,
      };
      animations.push(animation);
      return animation as Animation;
    }),
  });
});

afterEach(() => {
  if (originalAnimate) {
    Object.defineProperty(Element.prototype, "animate", {
      configurable: true,
      value: originalAnimate,
    });
  } else {
    delete (Element.prototype as Partial<Element>).animate;
  }
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: originalMatchMedia,
  });
});

function finish(animation: ControlledAnimation) {
  act(() => animation.onfinish?.call(
    animation as Animation,
    {} as AnimationPlaybackEvent,
  ));
}

describe("InlineReveal", () => {
  it("unrolls from the summary origin and keeps closing detail mounted until motion completes", () => {
    const view = render(
      <InlineReveal
        ariaLabel="完整消息"
        collapsedHeight={38}
        detail={<div>完整正文</div>}
        detailClassName="message-detail"
        expanded={false}
        expandedHeight={440}
        preview={<span>摘要正文</span>}
      />,
    );

    expect(screen.getByText("摘要正文")).toBeVisible();
    expect(screen.queryByRole("region", { name: "完整消息" })).not.toBeInTheDocument();

    view.rerender(
      <InlineReveal
        ariaLabel="完整消息"
        collapsedHeight={38}
        detail={<div>完整正文</div>}
        detailClassName="message-detail"
        expanded
        expandedHeight={440}
        preview={<span>摘要正文</span>}
      />,
    );

    const shell = screen.getByTestId("inline-reveal");
    expect(shell).toHaveAttribute("data-motion-phase", "opening");
    expect(screen.getByRole("region", { name: "完整消息" })).toBeVisible();
    expect(screen.getByText("摘要正文").closest(".inline-reveal-preview"))
      .toHaveAttribute("aria-hidden", "true");
    expect(Element.prototype.animate).toHaveBeenLastCalledWith(
      [{ height: "38px" }, { height: "440px" }],
      expect.objectContaining({ duration: DETAIL_OPEN_DURATION_MS, fill: "forwards" }),
    );

    finish(animations[0]);
    expect(shell).toHaveAttribute("data-motion-phase", "open");
    expect(screen.queryByText("摘要正文")).not.toBeInTheDocument();
    expect(screen.getAllByText("完整正文")).toHaveLength(1);

    view.rerender(
      <InlineReveal
        ariaLabel="完整消息"
        collapsedHeight={38}
        detail={<div>完整正文</div>}
        detailClassName="message-detail"
        expanded={false}
        expandedHeight={440}
        preview={<span>摘要正文</span>}
      />,
    );

    expect(shell).toHaveAttribute("data-motion-phase", "closing");
    expect(shell.querySelector(".inline-reveal-detail")).toBeInTheDocument();
    expect(Element.prototype.animate).toHaveBeenLastCalledWith(
      [{ height: "440px" }, { height: "38px" }],
      expect.objectContaining({ duration: DETAIL_CLOSE_DURATION_MS, fill: "forwards" }),
    );

    finish(animations[1]);
    expect(shell).toHaveAttribute("data-motion-phase", "closed");
    expect(shell.querySelector(".inline-reveal-detail")).not.toBeInTheDocument();
    expect(screen.getByText("摘要正文")).toBeVisible();
  });

  it("finishes immediately when reduced motion is requested", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({
        matches: true,
        media: "(prefers-reduced-motion: reduce)",
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }),
    });
    const view = render(
      <InlineReveal
        ariaLabel="Tool 完整详情"
        collapsedHeight={52}
        detail={<div>完整 Tool</div>}
        expanded={false}
        expandedHeight={520}
        preview={<span>Tool 摘要</span>}
      />,
    );

    view.rerender(
      <InlineReveal
        ariaLabel="Tool 完整详情"
        collapsedHeight={52}
        detail={<div>完整 Tool</div>}
        expanded
        expandedHeight={520}
        preview={<span>Tool 摘要</span>}
      />,
    );

    expect(screen.getByTestId("inline-reveal")).toHaveAttribute("data-motion-phase", "open");
    expect(screen.getByRole("region", { name: "Tool 完整详情" })).toBeVisible();
    expect(Element.prototype.animate).not.toHaveBeenCalled();
  });
});
