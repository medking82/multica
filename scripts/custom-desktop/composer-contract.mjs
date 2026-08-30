import assert from "node:assert/strict";
import ts from "typescript";

export const COMPOSERS = {
  ChatInput: "packages/views/chat/components/chat-input.tsx",
  CommentInput: "packages/views/issues/components/comment-input.tsx",
  ReplyInput: "packages/views/issues/components/reply-input.tsx",
  ManualCreatePanel: "packages/views/modals/create-issue.tsx",
  AgentCreatePanel: "packages/views/modals/quick-create-issue.tsx",
};

/** Inspect both source JSX and the unminified packaged React calls. A generic
 * slash marker is not evidence that every composer received the actual props. */
export function assertComposerContract(text, names = Object.keys(COMPOSERS), jsx = false) {
  const file = ts.createSourceFile(jsx ? "composer.tsx" : "renderer.js", text,
    ts.ScriptTarget.Latest, true, jsx ? ts.ScriptKind.TSX : ts.ScriptKind.JS);
  const bodies = new Map();
  function locate(node) {
    if (ts.isFunctionDeclaration(node) && node.name) bodies.set(node.name.text, node);
    ts.forEachChild(node, locate);
  }
  locate(file);
  for (const name of names) {
    const body = bodies.get(name);
    assert.ok(body, `Missing packaged composer ${name}`);
    const editors = [];
    const mics = [];
    function visit(node) {
      let tag;
      const props = new Map();
      if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
        tag = node.tagName.getText(file);
        for (const attribute of node.attributes.properties) {
          if (!ts.isJsxAttribute(attribute)) continue;
          props.set(attribute.name.getText(file), !attribute.initializer ? "true" :
            ts.isJsxExpression(attribute.initializer) ? attribute.initializer.expression?.getText(file) : attribute.initializer.getText(file));
        }
      } else if (ts.isCallExpression(node) && node.arguments.length > 1 &&
        ts.isIdentifier(node.arguments[0]) && ts.isObjectLiteralExpression(node.arguments[1])) {
        tag = node.arguments[0].text;
        for (const property of node.arguments[1].properties) {
          if (ts.isPropertyAssignment(property)) props.set(property.name.getText(file), property.initializer.getText(file));
          if (ts.isShorthandPropertyAssignment(property)) props.set(property.name.text, property.name.text);
        }
      }
      if (tag === "ContentEditor" && props.get("enableSlashCommands") === "true") editors.push(props.get("ref"));
      if (tag === "VoiceInputButton") mics.push(props.get("editorRef"));
      ts.forEachChild(node, visit);
    }
    visit(body);
    assert.ok(editors.some((ref) => ref && mics.includes(ref)), `${name}: slash and mic must target the same live editor`);
  }
}
