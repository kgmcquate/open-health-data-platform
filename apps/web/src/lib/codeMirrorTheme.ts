import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";

/** CodeMirror's built-in highlight styles hardcode colors (strings in a
 * brick red, keywords in magenta, …) that clash with the daisyUI `ohdp` /
 * `ohdp-dark` palette in index.css. This theme reads the same CSS custom
 * properties daisyUI sets on `[data-theme]`, so the editor re-skins itself
 * whenever the navbar theme controller flips `data-theme` — no JS listener
 * needed. */
const editorTheme = EditorView.theme({
  "&": {
    color: "var(--color-base-content)",
    backgroundColor: "var(--color-base-100)",
  },
  ".cm-content": {
    caretColor: "var(--color-base-content)",
  },
  ".cm-cursor, .cm-dropCursor": {
    borderLeftColor: "var(--color-base-content)",
  },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    {
      backgroundColor: "var(--color-primary)",
      opacity: 0.25,
    },
  ".cm-activeLine": {
    backgroundColor: "var(--color-base-200)",
  },
  ".cm-gutters": {
    color: "var(--color-base-content)",
    backgroundColor: "var(--color-base-100)",
    border: "none",
    opacity: 0.5,
  },
  ".cm-activeLineGutter": {
    backgroundColor: "var(--color-base-200)",
  },
});

const highlightStyle = HighlightStyle.define([
  { tag: t.comment, color: "var(--color-base-content)", opacity: 0.5, fontStyle: "italic" },
  { tag: [t.propertyName, t.definition(t.propertyName)], color: "var(--color-secondary)" },
  { tag: [t.string, t.special(t.string)], color: "var(--color-primary)" },
  { tag: [t.number, t.bool, t.null], color: "var(--color-accent)" },
  { tag: [t.keyword, t.operator, t.punctuation], color: "var(--color-base-content)" },
  { tag: t.invalid, color: "var(--color-error)" },
]);

/** Drop-in `extensions` entry for `<CodeMirror>` that matches the hub's daisyUI theme. */
export const ohdpCodeMirrorTheme = [editorTheme, syntaxHighlighting(highlightStyle)];
