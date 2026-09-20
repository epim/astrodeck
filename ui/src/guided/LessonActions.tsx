import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Keep lesson navigation in the wizard's fixed footer on short screens.
 * Standalone component previews retain the same controls inline. */
export function LessonActions({children}:{children:ReactNode}) {
  const [host,setHost]=useState<HTMLElement|null>(null);
  useEffect(()=>{setHost(document.getElementById("guided-lesson-actions"));},[]);
  const actions=<div className="guided-lesson-actions">{children}</div>;
  return host?createPortal(actions,host):actions;
}
