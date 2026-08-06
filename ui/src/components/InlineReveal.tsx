/** Animate one compact summary into its complete inline detail.
 *
 * The component owns only browser-local presentation state. Its compact and
 * expanded heights come from the deterministic canvas geometry, while the
 * complete detail remains mounted during closing so text, tabs, and scroll
 * content disappear as one continuous surface instead of blinking away.
 */

import type { CSSProperties, ReactNode } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

export const DETAIL_OPEN_DURATION_MS = 300;
export const DETAIL_CLOSE_DURATION_MS = 200;
export const DETAIL_OPEN_EASING = "cubic-bezier(0.215, 0.61, 0.355, 1)";
export const DETAIL_CLOSE_EASING = "cubic-bezier(0.645, 0.045, 0.355, 1)";

export type DetailMotionPhase = "closed" | "opening" | "open" | "closing";

interface InlineRevealProps {
  ariaLabel: string;
  collapsedHeight: number;
  detail: ReactNode;
  detailClassName?: string;
  expanded: boolean;
  expandedHeight: number;
  preview: ReactNode;
}

interface MotionTiming {
  duration: number;
  easing: string;
}

/** Return the Ant Design-aligned duration and easing for one detail change. */
export function detailMotionTiming(expanded: boolean): MotionTiming {
  return expanded
    ? { duration: DETAIL_OPEN_DURATION_MS, easing: DETAIL_OPEN_EASING }
    : { duration: DETAIL_CLOSE_DURATION_MS, easing: DETAIL_CLOSE_EASING };
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/** Reveal complete content from the compact preview's exact visual origin. */
export function InlineReveal({
  ariaLabel,
  collapsedHeight,
  detail,
  detailClassName = "",
  expanded,
  expandedHeight,
  preview,
}: InlineRevealProps) {
  const [phase, setPhase] = useState<DetailMotionPhase>(expanded ? "open" : "closed");
  const regionRef = useRef<HTMLDivElement>(null);
  const animationRef = useRef<Animation | null>(null);
  const expandedRef = useRef(expanded);
  const motionSequenceRef = useRef(0);
  const initialHeightRef = useRef(expanded ? expandedHeight : collapsedHeight);
  const timing = detailMotionTiming(expanded);

  useLayoutEffect(() => {
    const region = regionRef.current;
    if (!region || expandedRef.current === expanded) return;

    const wasExpanded = expandedRef.current;
    expandedRef.current = expanded;
    const sequence = ++motionSequenceRef.current;
    const fallbackHeight = wasExpanded ? expandedHeight : collapsedHeight;
    const measuredHeight = region.getBoundingClientRect().height || fallbackHeight;
    const targetHeight = expanded ? expandedHeight : collapsedHeight;

    // Capture the current interpolated height before cancelling. A fast
    // reverse click can therefore continue from the visible frame instead of
    // restarting at the previous endpoint.
    animationRef.current?.cancel();
    animationRef.current = null;
    region.style.height = `${measuredHeight}px`;

    if (prefersReducedMotion() || typeof region.animate !== "function") {
      region.style.height = `${targetHeight}px`;
      setPhase(expanded ? "open" : "closed");
      return;
    }

    setPhase(expanded ? "opening" : "closing");
    const animation = region.animate(
      [{ height: `${measuredHeight}px` }, { height: `${targetHeight}px` }],
      { duration: timing.duration, easing: timing.easing, fill: "forwards" },
    );
    animationRef.current = animation;
    animation.onfinish = () => {
      if (sequence !== motionSequenceRef.current) return;
      region.style.height = `${targetHeight}px`;
      animationRef.current = null;
      setPhase(expanded ? "open" : "closed");
    };
  }, [collapsedHeight, expanded, expandedHeight, timing.duration, timing.easing]);

  useEffect(() => () => {
    motionSequenceRef.current += 1;
    animationRef.current?.cancel();
    animationRef.current = null;
  }, []);

  const renderPreview = !expanded || phase !== "open";
  const renderDetail = expanded || phase !== "closed";
  const style = {
    height: `${initialHeightRef.current}px`,
    "--detail-motion-duration": `${timing.duration}ms`,
    "--detail-motion-easing": timing.easing,
  } as CSSProperties;

  return (
    <div
      className="inline-reveal"
      data-expanded={expanded ? "true" : "false"}
      data-motion-phase={phase}
      data-testid="inline-reveal"
      onWheel={(event) => event.stopPropagation()}
      ref={regionRef}
      style={style}
    >
      {renderPreview && (
        <div aria-hidden={expanded} className="inline-reveal-preview">
          {preview}
        </div>
      )}
      {renderDetail && (
        <section
          aria-hidden={!expanded}
          aria-label={ariaLabel}
          className={["inline-reveal-detail", detailClassName].filter(Boolean).join(" ")}
          inert={!expanded}
        >
          {detail}
        </section>
      )}
    </div>
  );
}
